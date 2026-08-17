"""Where do the pieces already sit in composition space?

Before asking a campaign to explore a space, ask what the series has already covered.
Every accepted spec is a sample; this reads all of them and reports the spread along
the axes that decide what a picture looks like from where it is taken:

  standoff    |camera x| — how far to the side of the line the camera stands. The
              line runs up x=0, so 0 puts the viewer on the axis and the towers
              stack into mirror symmetry; stepping aside separates them into a
              diagonal recession and sends the wires across the frame.
  head-on     |fwd . y| — where the camera *aims*, against the axis. Not the same
              question: 028 aims almost straight down the line (0.99) from 35 m to
              the side, and that is the composition the README leads with.
  anchor      the nearest tower's share of the frame height — the same quantity the
              validator gates on for landscape pieces.
  length      towers x mean span, in metres: how much line the picture claims.

A hole in the spread is a question for a campaign. An even spread with one outlier is
a threshold in checks.json. Everything piled in one corner means the rules are
choosing the composition, whatever the axes say.

Usage:
  python scripts/survey_composition.py
  python scripts/survey_composition.py --axis head-on   # sorted list, one axis
"""

import argparse
import glob
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pylon3d  # noqa: E402
import ruleset  # noqa: E402
from colorutil import delta_e  # noqa: E402
from validate_pylon import camera_basis  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULESET = "pylon-series"


def measure(spec):
    """One spec -> its coordinates in composition space."""
    cam = spec["camera"]
    pos, aim, lens = cam["pos"], cam["aim"], cam["lens"]
    fwd, _right, _up = camera_basis(pos, aim)

    count = int(spec["towers"])
    span = spec["span"]
    ys = pylon3d.tower_ys(count, span)
    terrain = spec.get("terrain")
    elevations = pylon3d.tower_elevations(terrain, count, span) or [0.0] * count

    # the anchor is the tower nearest the camera — the one the picture is about
    ni = min(range(len(ys)), key=lambda i: abs(ys[i] - pos[1]))
    scale = spec.get("tower_scale", 1.0)
    top = (pylon3d.HEIGHT + pylon3d.PEAK_H) * scale
    d_peak = math.dist(pos, (0.0, ys[ni], top + elevations[ni]))

    spans = span if isinstance(span, list) else [span] * (count - 1)
    palette = spec.get("palette") or {}
    return {
        "standoff": abs(pos[0]),                    # the line runs up x=0
        "head_on": abs(fwd[1]),                     # ...and along +y
        "palette_anchor": list(palette.get("horizon")
                               or spec["sky"]["horizon"]),
        "anchor": (top * lens) / (36.0 * d_peak),
        "length": count * (sum(spans) / len(spans)),
        "kind": (terrain or {}).get("kind") or "bare sky",
        "lens": lens,
        "towers": count,
    }


def same_picture(a, b, feed):
    """Are these two measurements the same picture twice?

    Not a distance with weights — a conjunction. Two pieces are a repeat only when
    every axis that decides what a picture looks like sits inside its own tolerance,
    so one clearly different axis is enough to make them different pictures. Each
    tolerance is then a number a reader can argue with, which a weighted sum is not.
    """
    if feed.get("same_kind_only", True) and a["kind"] != b["kind"]:
        return False
    return (delta_e(a["palette_anchor"], b["palette_anchor"]) < feed["repeat_color_de"]
            and abs(a["standoff"] - b["standoff"]) < feed["repeat_standoff_m"]
            and abs(a["head_on"] - b["head_on"]) < feed["repeat_head_on"])


def feed_selection(measured, feed):
    """Walk the pieces in numeric order and keep the ones the feed will show.

    The series keeps everything the rules produced; the feed shows one piece from each
    region of the space — the same idea the scout loop uses when it keeps one elite per
    niche, applied to what the account publishes. A profile grid is seen all at once,
    so "already shown" has to mean ever, not lately.

    Returns (shown, {skipped: the piece it repeats}). Because posting walks the numbers
    in order, replaying that walk offline reproduces the live decision exactly — which
    is what lets the manifest name the passed-over pieces without asking the API.
    """
    shown, skipped = [], {}
    for n in sorted(measured):
        dup = next((m for m in shown if same_picture(measured[n], measured[m], feed)),
                   None)
        if dup is None:
            shown.append(n)
        else:
            skipped[n] = dup
    return shown, skipped


def measured_pieces():
    """Every accepted piece, keyed by number, in composition space."""
    out = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "scenes", RULESET, "[0-9]*.json"))):
        with open(path, encoding="utf-8") as f:
            out[int(os.path.basename(path)[:3])] = measure(json.load(f))
    return out


def histogram(values, lo, hi, bins=10, width=44):
    """A text histogram — the holes are the point, so empty bins must be visible."""
    counts = [0] * bins
    for v in values:
        i = min(bins - 1, max(0, int((v - lo) / (hi - lo) * bins)))
        counts[i] += 1
    peak = max(counts) or 1
    out = []
    for i, c in enumerate(counts):
        edge = lo + (hi - lo) * i / bins
        bar = "#" * round(c / peak * width)
        out.append(f"  {edge:6.2f} |{bar:<{width}}| {c or '':>3}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--axis", choices=["standoff", "head-on", "anchor", "length"],
                    help="list every piece along one axis instead of the histograms")
    args = ap.parse_args()

    rows = []
    for path in sorted(glob.glob(os.path.join(ROOT, "scenes", RULESET, "[0-9]*.json"))):
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
        m = measure(spec)
        m["piece"] = int(os.path.basename(path)[:3])
        rows.append(m)

    if args.axis:
        key = args.axis.replace("-", "_")
        for r in sorted(rows, key=lambda r: r[key]):
            print(f"  {r['piece']:03d}  {r[key]:8.3f}  {r['kind']:<10} "
                  f"lens {r['lens']:>2}  {r['towers']} towers")
        return

    # Bounds come from the rules, not from the data, so an empty bin means "the rules
    # allow this and the series has never gone there" — which is the only kind of hole
    # worth a campaign. Choosing the bounds by eye makes a narrow-by-design axis look
    # collapsed.
    cfg = ruleset.load(RULESET)["pylon"]
    t_lo, t_hi = cfg["towers"]
    s_lo, s_hi = cfg["span"]

    print(f"[survey] {len(rows)} pieces; bounds are what the rules permit\n")
    for label, key, lo, hi in (
            ("standoff (metres to the side of the line) — ungoverned",
             "standoff", 0.0, 40.0),
            ("head-on  (1.0 = aimed down the line) — ungoverned, and not the same "
             "question", "head_on", 0.0, 1.0),
            ("anchor   (nearest tower's share of frame height)", "anchor", 0.0, 1.0),
            ("length   (towers x mean span, metres)",
             "length", t_lo * s_lo, t_hi * s_hi)):
        vals = [r[key] for r in rows]
        print(label)
        print(histogram(vals, lo, hi))
        print(f"  min {min(vals):.2f}  median {sorted(vals)[len(vals) // 2]:.2f}  "
              f"max {max(vals):.2f}\n")

    used = sorted({r["towers"] for r in rows})
    unused = [n for n in range(t_lo, t_hi + 1) if n not in used]
    print(f"tower counts used: {used}" +
          (f" — permitted but never sampled: {unused}" if unused else ""))
    on_axis = [r for r in rows if r["standoff"] < 5.0]
    print(f"standing on the axis (standoff < 5 m): {len(on_axis)} of {len(rows)} — "
          + ", ".join(f"{r['piece']:03d}" for r in sorted(
              on_axis, key=lambda r: r["standoff"])[:12])
          + (" ..." if len(on_axis) > 12 else ""))


if __name__ == "__main__":
    main()
