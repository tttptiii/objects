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

# Validator messages carry em-dashes and ΔE; a cp949 console must not kill the batch.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STRIDE = 137    # coprime to the design-space size — consecutive pieces walk the
                # whole combination space in a decorrelated order


def load_directions(rs):
    with open(os.path.join(rs.directory, "directions.json"), encoding="utf-8") as f:
        return json.load(f)


def direction_for(piece, dirs):
    """Piece number -> (direction text, required terrain kind, mood palette bounds).

    Pieces listed under `assigned` keep their hand-written brief forever — resampling
    never reshuffles them. Every other piece is a point in the design space: the
    combination index walks the axes' cross-product with a fixed stride, and the mood
    axis contributes palette-anchor bounds that the sampler enforces — which is what
    guarantees color spread across a batch."""
    a = dirs["assigned"].get(str(piece))
    if a:
        return a["text"], a.get("kind"), None
    axes = dirs["axes"]
    sizes = [len(axes["stage"]), len(axes["mood"]), len(axes["density"]),
             len(axes["composition"]), len(axes["line"])]
    total = 1
    for s in sizes:
        total *= s
    idx = (piece * STRIDE) % total
    digits = []
    for s in sizes:
        digits.append(idx % s)
        idx //= s
    stage = axes["stage"][digits[0]]
    mood = axes["mood"][digits[1]]
    density = axes["density"][digits[2]]
    comp = axes["composition"][digits[3]]
    line = axes["line"][digits[4]]
    comp_text = comp["sky"] if stage["kind"] is None else comp["land"]
    h, li = mood["hue"], mood["lightness"]
    text = (f"{stage['text']} {mood['text']} {density['text']} {comp_text} "
            f"{line['text']} Anchor the palette hue between {h[0]} and {h[1]} "
            f"degrees, lightness {li[0]}-{li[1]}.")
    return text, stage["kind"], {"hue": h, "lightness": li}


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


def sample_one(idx, brief, rs, model, effort, out_dir, today, retries):
    cfg = rs["pylon"]
    direction, req_kind, bounds = brief
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
        anchor = (spec.get("palette") or {}).get("horizon")
        if bounds and isinstance(anchor, list) and len(anchor) == 3:
            (h0, h1), (l0, l1) = bounds["hue"], bounds["lightness"]
            if not h0 <= anchor[2] <= h1:
                issues.append(f"palette.horizon hue {anchor[2]} is outside this "
                              f"piece's mood band {h0}-{h1} degrees")
            if not l0 <= anchor[0] <= l1:
                issues.append(f"palette.horizon lightness {anchor[0]} is outside "
                              f"this piece's mood band {l0}-{l1}")
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

    dirs = load_directions(rs)
    print(f"[pylon] rule set '{rs.name}' · {len(targets)} pieces · {args.jobs} in parallel")
    with futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        results = list(ex.map(
            lambda n: sample_one(n, direction_for(n, dirs), rs,
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
