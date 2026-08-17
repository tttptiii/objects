"""Drive Blender over a batch of pylon-series specs — one call, loud failures.

Ad-hoc shell loops around Blender proved error-prone: a failed render exits quietly
and the previous image stays on disk looking current. This driver checks the exit
code and verifies the output file was actually (re)written, and says so.

Usage:
  python scripts/render_pylons.py --pieces 24-31,36        # draft renders
  python scripts/render_pylons.py --pieces 30 --full       # full-resolution render
  python scripts/render_pylons.py --demos                  # the _demo3d_* scenes

Blender is found via the BLENDER environment variable, else "blender" on PATH.
"""

import argparse
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDER_SCRIPT = os.path.join(ROOT, "scripts", "render_pylon3d.py")

# The code that turns a spec into pixels. `rules_sha` pins the prompt that produced a
# spec; this pins the renderer that produced an image, which is the other half of the
# reproducibility claim and was missing until the conductors gained a trailing span:
# every render changed while every spec stayed byte-identical, and nothing recorded it.
RENDERER_SOURCES = ("pylon3d.py", "render_pylon3d.py")
STAMP = ".render_sha"


def render_sha():
    """Short content hash of the renderer. Read as text, so a CRLF checkout and an LF
    one agree — the same normalization `ruleset.sha` already relies on."""
    h = hashlib.sha256()
    for name in RENDERER_SOURCES:
        with open(os.path.join(ROOT, "scripts", name), encoding="utf-8") as f:
            h.update(f.read().encode("utf-8"))
    return h.hexdigest()[:12]


def stamped_sha(out_dir):
    """The renderer that last wrote into this output directory, or None."""
    path = os.path.join(out_dir, STAMP)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read().strip() or None


def parse_pieces(text):
    out = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def render(blender, spec_path, out_path, draft):
    before = os.path.getmtime(out_path) if os.path.exists(out_path) else None
    cmd = [blender, "--background", "--factory-startup",
           "--python", RENDER_SCRIPT, "--", spec_path, out_path]
    if draft:
        cmd.append("--draft")
    r = subprocess.run(cmd, capture_output=True, text=True)
    fresh = (os.path.exists(out_path)
             and os.path.getmtime(out_path) != before)
    if r.returncode != 0 or not fresh:
        print(f"[render] FAILED {spec_path} (exit {r.returncode}, "
              f"{'stale output' if not fresh else 'wrote'})")
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-12:]
        for line in tail:
            print(f"    {line}")
        return False
    print(f"[render] {spec_path} -> {out_path}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pieces", help="e.g. 24-31,36")
    ap.add_argument("--demos", action="store_true", help="render scenes/_demo3d_*.json")
    ap.add_argument("--ruleset", default="pylon-series")
    ap.add_argument("--full", action="store_true", help="full resolution (else draft)")
    args = ap.parse_args()
    if not args.pieces and not args.demos:
        ap.error("nothing to render — pass --pieces and/or --demos")

    blender = os.environ.get("BLENDER", "blender")
    sub = "full" if args.full else "draft"
    jobs = []
    if args.pieces:
        # Piece numbers have gaps — a retired direction era leaves its numbers empty
        # forever — so a range is a convenience, not a claim that every number in it
        # exists. Absent numbers are dropped rather than counted as failures, which
        # is also what lets "every job succeeded" mean something.
        missing = []
        for n in parse_pieces(args.pieces):
            spec = os.path.join(ROOT, "scenes", args.ruleset, f"{n:03d}.json")
            if not os.path.exists(spec):
                missing.append(n)
                continue
            jobs.append((spec, os.path.join(ROOT, "outputs", args.ruleset, sub,
                                            f"{n:03d}.png")))
        if missing:
            print(f"[render] no spec for {len(missing)} requested number(s), skipped: "
                  + ", ".join(f"{n:03d}" for n in missing[:12])
                  + (" ..." if len(missing) > 12 else ""))
        if not jobs and not args.demos:
            sys.exit("[render] nothing to render — no spec matched --pieces")
    if args.demos:
        demo_dir = os.path.join(ROOT, "scenes")
        for name in sorted(os.listdir(demo_dir)):
            if name.startswith("_demo3d_") and name.endswith(".json"):
                jobs.append((os.path.join(demo_dir, name),
                             os.path.join(ROOT, "outputs", args.ruleset, "demo",
                                          name[len("_demo3d_"):-len(".json")] + ".png")))

    ok = sum(render(blender, s, o, draft=not args.full) for s, o in jobs)
    # Stamp every directory this run wrote into, so a later reader can tell which
    # renderer made the images sitting there. Only on a clean sweep: a partial batch
    # leaves a mixture, and a stamp claiming otherwise is worse than none.
    if ok == len(jobs):
        sha = render_sha()
        for out_dir in sorted({os.path.dirname(o) for _s, o in jobs}):
            with open(os.path.join(out_dir, STAMP), "w", encoding="utf-8") as f:
                f.write(sha + "\n")
    print(f"[render] done {ok}/{len(jobs)}" + (f" · renderer {render_sha()}" if ok else ""))
    if ok < len(jobs):
        sys.exit(1)


if __name__ == "__main__":
    main()
