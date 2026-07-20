"""Sample pylon-series specs — the model decides camera, line rhythm, sky, and fog; the
tower geometry is guaranteed by pylon3d.py. Validation is pure Python (validate_pylon.py),
including a frustum check that the camera actually frames the subject.

Usage:
  python scripts/sample_pylons.py --count 8
  python scripts/sample_pylons.py --only 2,5
"""

import argparse
import concurrent.futures as futures
import datetime
import json
import os
import sys
import threading
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ruleset  # noqa: E402
from colorutil import RELATIONS, amount_issues, derive, oklch_range_issues  # noqa: E402
from llm import DEFAULT_MODEL, call_claude, extract_json  # noqa: E402
from validate_pylon import check  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_lock = threading.Lock()

DIRECTIONS = [
    "Directly beneath the tower, wide lens, looking straight up through the lattice. "
    "Cool blue-gray fog.",
    "Beside the base, looking up along the line — the next towers ghosting in dense fog.",
    "Warm dawn haze; the tower nearly black against apricot. A heavy trunk line — "
    "tower_scale 1.1 or more.",
    "Farther back with a longer lens, two towers stacking into each other in the haze.",
    "Extreme wide at the legs, the tower plunging past the top of the frame.",
    "Thin winter haze — a long clear distance, three towers visible receding. A "
    "light rural line: tower_scale near 0.9, an uneven span rhythm.",
    "Camera off to the side of the line, the wires crossing the frame diagonally overhead.",
    "Dense fog at its thickest: only the subject survives, the second tower barely a stain.",
    "Snowfield: white ground one breath away from the sky, drift shadows barely there, "
    "dark steel rising out of the white.",
    "Snowfield in blue dusk haze: blue-gray fog over snow, two small rural towers "
    "(tower_scale about 0.9), the far one almost erased.",
    "Snowfield whiteout: the horizon gone — ground and sky a single white, only the "
    "near tower fully drawn.",
    "Snowfield in thin winter light: three towers receding, wires long and low over "
    "the drifts.",
    "Mountain pass: the first tower large and crisp at the frame's edge, the line "
    "diving away into layered ranges, every far tower still countable. Heavy trunk "
    "towers — tower_scale 1.1+, V-string insulators.",
    "Mountains at dawn: warm haze, the anchor tower dark and near, the line stepping "
    "away down the valley, ridges stacked behind.",
    "Beside the anchor tower in mountain country: wires leading the eye to the far "
    "towers threading between two ranges. V-string insulators read at this distance.",
    "High mountain fog: the near tower sharp against the ridge, each tower behind it "
    "one tone lighter, the last almost — but not — gone.",
    "Farmland at dawn: pink horizon under a blue-gray zenith, the anchor tower dark "
    "over a pale crop field, ground mist pooled behind the hedgerow.",
    "Farmland, morning blue: cool haze, gaps in the hedge showing the misty flats, "
    "the line stepping away over low hills on an uneven span rhythm, V-strings.",
    "Farmland at dusk: straw-colored field, dark hedge band, the far towers rising "
    "out of the mist one tone paler each.",
    "Wide farmland: low hills behind the hedgerow, deep mist between them, wires "
    "sweeping over the field toward the anchor.",
]


def direction_index(piece):
    """Piece number -> direction index, as a fixed table. A modulo of the DIRECTIONS
    list length silently reshuffles every existing piece each time the list grows —
    it did, twice. These ranges are frozen; extend with new explicit ranges."""
    if 24 <= piece <= 31:
        return 8 + (piece - 24)     # 8-11 snowfield, 12-15 mountains
    if 36 <= piece <= 39:
        return 16 + (piece - 36)    # farmland
    return piece % 8                # bare-sky rotation


def required_kind(direction_idx):
    """Directions 8-19 are landscape directions — the terrain block is mandatory and
    its kind is fixed by the direction. Prose alone proved insufficient."""
    if 8 <= direction_idx <= 11:
        return "snowfield"
    if 12 <= direction_idx <= 15:
        return "mountains"
    if 16 <= direction_idx <= 19:
        return "farmland"
    return None


def resolve_palette(spec):
    """Optional `palette` block -> concrete colors written into the spec. Returns issues.

    One anchor (the horizon fog color) plus named relations for the rest formalizes
    the palette: every color is a declared move away from the fog, so the family is
    coherent by construction. Resolution is deterministic and happens before
    validation — the derived colors still face every contrast gate."""
    pal = spec.get("palette")
    if not pal:
        return ["the palette block is required — declare all colors as one anchor "
                "plus named moves (see the Palette section)"]
    issues = list(oklch_range_issues("palette.horizon", pal.get("horizon")))
    if issues:
        return issues
    anchor = pal["horizon"]
    sky = spec.setdefault("sky", {})
    sky["horizon"] = anchor
    terrain = spec.get("terrain") or {}
    slots = {
        "zenith": (sky, "zenith"),
        "steel": (spec, "steel"),
        "terrain": (terrain,
                    "field" if terrain.get("kind") == "farmland" else "color"),
    }
    for name, (target, key) in slots.items():
        move = pal.get(name)
        if move is None:
            continue
        if isinstance(move, dict):
            rel = move.get("relation")
            if rel not in RELATIONS:
                issues.append(f"palette.{name}.relation = {rel!r} — must be one of "
                              f"{sorted(RELATIONS)}")
                continue
            bad = amount_issues(f"palette.{name}", rel, move.get("amount"))
            issues.extend(bad)
            if not bad:
                target[key] = derive(anchor, rel, move.get("amount"))
        else:
            bad = oklch_range_issues(f"palette.{name}", move)
            issues.extend(bad)
            if not bad:
                target[key] = move
    return issues


def log(msg):
    with _lock:
        print(msg, flush=True)


def sample_one(idx, direction, rs, model, effort, out_dir, today, retries):
    cfg = rs["pylon"]
    req_kind = required_kind(direction_index(idx))
    base = (f"{rs.rules}\n\nDirection for this piece: {direction}\n\n"
            "Emit one JSON object and nothing else.")
    session = str(uuid.uuid4())
    retry = None
    for attempt in range(1, retries + 1):
        try:
            text = call_claude(retry if retry else base, model, session=session,
                               resume=retry is not None, effort=effort)
            spec = extract_json(text)
            if not isinstance(spec, dict):
                raise ValueError("spec is not an object")
        except Exception as e:
            retry = f"That failed: {type(e).__name__}: {e}. Emit one JSON object only."
            continue
        issues = resolve_palette(spec)
        issues += check(spec, cfg)
        if req_kind and (spec.get("terrain") or {}).get("kind") != req_kind:
            issues.insert(0, (f"this piece's direction requires terrain.kind "
                              f"'{req_kind}' — include that terrain block and satisfy "
                              "its framing rules; dropping the landscape is not an "
                              "option"))
        if not issues:
            spec["meta"] = {"work": idx, "ruleset": rs.name, "rules_sha": rs.sha,
                            "model": model, "date": today, "direction": direction}
            path = os.path.join(out_dir, f"{idx:03d}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(spec, f, ensure_ascii=False, indent=2)
                f.write("\n")
            log(f"[pylon] #{idx:03d} ok on attempt {attempt}")
            return path
        rej = os.path.join(out_dir, "rejected")
        os.makedirs(rej, exist_ok=True)
        spec["meta"] = {"work": idx, "attempt": attempt, "rejected_for": issues}
        with open(os.path.join(rej, f"{idx:03d}-a{attempt}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False, indent=2)
            f.write("\n")
        log(f"[pylon] #{idx:03d} attempt {attempt}: {len(issues)} violation(s) — "
            f"{issues[0][:72]}")
        retry = ("What you just produced was rejected for the following violations. Emit "
                 "the corrected JSON object in full, and nothing else:\n- "
                 + "\n- ".join(issues))
    log(f"[pylon] #{idx:03d} failed")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ruleset", default="pylon-series")
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--only")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--max-retries", type=int, default=5)
    args = ap.parse_args()

    rs = ruleset.load(args.ruleset)
    if not rs.get("pylon"):
        sys.exit(f"rule set '{rs.name}' has no pylon config")
    out_dir = os.path.join(ROOT, "scenes", args.ruleset)
    os.makedirs(out_dir, exist_ok=True)
    today = datetime.date.today().isoformat()
    targets = ([int(n) for n in args.only.split(",")] if args.only
               else list(range(args.count)))

    print(f"[pylon] rule set '{rs.name}' · {len(targets)} pieces · {args.jobs} in parallel")
    with futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        results = list(ex.map(
            # direction keyed by the piece number through a frozen table, so a
            # resample always keeps its direction
            lambda n: sample_one(n, DIRECTIONS[direction_index(n)], rs,
                                 args.model, args.effort, out_dir, today,
                                 args.max_retries),
            targets))
    ok = sum(1 for r in results if r)
    print(f"[pylon] done {ok}/{len(targets)} -> scenes/{args.ruleset}/")
    failed = [t for t, r in zip(targets, results) if not r]
    if failed:
        print(f"[pylon] failed: {failed} — rerun with --only {','.join(map(str, failed))}")


if __name__ == "__main__":
    main()
