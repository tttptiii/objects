"""Color campaign: map the palette space by evolution, MAP-Elites style.

Composition is frozen — each individual is a palette genome evaluated on one of a
fixed set of accepted-piece chassis. The palette block (anchor + named moves, the
series' own grammar) is the genome; mutation perturbs it, crossover recombines it,
the validator gates it (with the sunset cap relaxed for this probe), Blender renders
a draft, and a vision judge (claude, reading the image) scores it. The niche map
keeps the best individual per (anchor hue x anchor lightness x steel chromaticity)
cell — the output is a terrain map of the color space, not a single winner.

Discoveries feed back into rules.md by hand — evolution here is a scout, not the
artist. Specs record campaign, generation, parents, and judge verdicts in meta.

Usage:
  python scripts/evolve_colors.py --init          # seed the archive from iter-0
  python scripts/evolve_colors.py --gen           # breed, render, judge one generation
  python scripts/evolve_colors.py --report        # write the niche map (click to zoom,
                                                  #  1-10 author scoring, export JSON)
  python scripts/evolve_colors.py --ingest F.json # take the exported author scores:
                                                  #  they override the judge's, elites
                                                  #  are re-ranked, agreement measured
"""

import argparse
import concurrent.futures as futures
import copy
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pylon3d  # noqa: E402  (unused directly; keeps import errors early)
import ruleset  # noqa: E402
from colorutil import AMOUNT_RANGES, RELATIONS, oklch_to_hex  # noqa: E402
from llm import DEFAULT_MODEL, call_claude, extract_json  # noqa: E402
from render_pylons import render  # noqa: E402
from sample_pylons import resolve_palette  # noqa: E402  (reconfigures stdout utf-8)
from validate_pylon import check  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMPAIGN = "color-01"
CDIR = os.path.join(ROOT, "scenes", "evolve", CAMPAIGN)
ODIR = os.path.join(ROOT, "outputs", "evolve", CAMPAIGN)

# Accepted pieces whose compositions serve as frozen chassis — two per stage.
CHASSIS = [2, 72, 25, 69, 28, 82, 39, 71]
GEN_SIZE = 20
MUTATION_TRIES = 10   # attempts to breed a validator-passing child per slot

# The probe relaxes exactly one identity clamp: the sunset cap. Everything else
# (steel-vs-sky contrast, terrain gates) stays — those gates are relative, so dark
# and inverted palettes pass on their own merits.
GATE_OVERRIDES = {"max_horizon_zenith_de": 0.30, "max_farm_horizon_zenith_de": 0.35}

JUDGE_PROMPT = """\
You are judging one image from a generative art series: lattice transmission towers \
in fog — flat graphic emission-only rendering (no lights), low-poly land, palette \
colors rendering as exactly themselves. This is a COLOR exploration on a known-good \
composition chassis: judge only what the palette does.

Score 0-10:
- coherence: the colors read as one family, one atmosphere (weight this most)
- subject legibility: the towers stand clearly against the sky
- territory: a distinct, striking mood in NEW ground (dark, dusk, inverted values, \
colored steel, near-sunset) beats a safe repeat of the series' usual pale fog — but \
only if it stays coherent and unmistakably of the same quiet series. The author's \
recorded taste: quiet over dramatic; fog atmosphere, not weather drama.

Read the image at: {path}

Reply with only JSON: {{"score": <0-10, one decimal>, "why": "<one short sentence>"}}"""


def load_cfg():
    cfg = dict(ruleset.load("pylon-series")["pylon"])
    cfg.update(GATE_OVERRIDES)
    return cfg


def genome_from(spec):
    """Palette genome out of a spec — from its palette block when it has one, else
    reconstructed as explicit colors (the pre-grammar pieces)."""
    pal = spec.get("palette") or {}
    g = {"horizon": list(pal.get("horizon") or spec["sky"]["horizon"])}
    for k, fallback in (("zenith", spec.get("sky", {}).get("zenith")),
                        ("steel", spec.get("steel"))):
        v = pal.get(k, fallback)
        g[k] = dict(v) if isinstance(v, dict) else list(v)
    t = spec.get("terrain") or {}
    if t:
        v = pal.get("terrain") or (t.get("field") if t.get("kind") == "farmland"
                                   else t.get("color"))
        g["terrain"] = (dict(v) if isinstance(v, dict)
                        else (list(v) if v else {"relation": "shade", "amount": 0.3}))
    return g


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def mutate(g, rng):
    """Perturb 1-3 genes. The anchor drifts in OKLCH; a slot either changes its
    relation, re-tunes its amount, or goes explicit (how colored steel and inverted
    values enter the pool)."""
    g = copy.deepcopy(g)
    slots = [k for k in ("zenith", "steel", "terrain") if k in g]
    for _ in range(rng.randint(1, 3)):
        gene = rng.choice(["horizon", "horizon"] + slots)   # anchor mutates most
        if gene == "horizon":
            L, C, H = g["horizon"]
            g["horizon"] = [_clamp(L + rng.gauss(0, 0.10), 0.25, 0.97),
                            _clamp(C + rng.gauss(0, 0.05), 0.0, 0.15),
                            (H + rng.gauss(0, 40.0)) % 360.0]
        else:
            r = rng.random()
            if r < 0.5:                                     # new named move
                rel = rng.choice(sorted(RELATIONS))
                lo, hi, _ = AMOUNT_RANGES.get(rel, (0.0, 1.0, ""))
                g[gene] = {"relation": rel, "amount": round(rng.uniform(lo, hi), 3)}
            elif r < 0.8 and isinstance(g[gene], dict):     # re-tune the amount
                rel = g[gene].get("relation", "muted")
                lo, hi, _ = AMOUNT_RANGES.get(rel, (0.0, 1.0, ""))
                g[gene]["amount"] = round(_clamp(
                    g[gene].get("amount", (lo + hi) / 2) + rng.gauss(0, (hi - lo) / 5),
                    lo, hi), 3)
            else:                                           # explicit color
                cap = {"zenith": 0.20, "steel": 0.30, "terrain": 0.25}[gene]
                g[gene] = [round(rng.uniform(0.15, 0.92), 3),
                           round(rng.uniform(0.0, cap), 3),
                           round(rng.uniform(0.0, 360.0), 1)]
    return g


def crossover(a, b, rng):
    g = {"horizon": list(a["horizon"])}
    for k in ("zenith", "steel", "terrain"):
        src = rng.choice([a, b])
        if k in src:
            g[k] = copy.deepcopy(src[k])
        elif k in a:
            g[k] = copy.deepcopy(a[k])
    return g


def build_spec(chassis_spec, genome, meta):
    spec = copy.deepcopy(chassis_spec)
    genome = copy.deepcopy(genome)
    if not spec.get("terrain"):
        genome.pop("terrain", None)
    spec["palette"] = genome
    (spec.get("sky") or {}).pop("cloud_color", None)   # re-derive from the new anchor
    spec["meta"] = meta
    return spec


def niche_key(spec):
    L, _C, H = spec["palette"]["horizon"]
    sL, sC, _sH = spec["steel"]
    hue = int(H // 60) % 6
    light = 0 if L < 0.55 else (1 if L < 0.72 else (2 if L < 0.88 else 3))
    steel = "colored" if sC > 0.06 else "neutral"
    return f"h{hue}-l{light}-{steel}"


def judge(png_path, model):
    text = call_claude(JUDGE_PROMPT.format(path=os.path.abspath(png_path)),
                       model, effort="low", allowed_tools=["Read"])
    v = extract_json(text)
    return _clamp(float(v["score"]), 0.0, 10.0), str(v.get("why", ""))[:300]


def _paths():
    archive = os.path.join(CDIR, "archive.json")
    log = os.path.join(CDIR, "log.jsonl")
    return archive, log


def load_archive():
    path, _ = _paths()
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(archive, entries):
    apath, lpath = _paths()
    os.makedirs(CDIR, exist_ok=True)
    with open(apath, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=1)
    with open(lpath, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def cmd_init(model):
    cfg = load_cfg()
    archive, entries = {"campaign": CAMPAIGN, "generation": 0, "cells": {}}, []
    with futures.ThreadPoolExecutor(max_workers=4) as ex:
        jobs = {}
        for n in CHASSIS:
            spec_path = os.path.join(ROOT, "scenes", "pylon-series", f"{n:03d}.json")
            png = os.path.join(ROOT, "outputs", "pylon-series", "draft", f"{n:03d}.png")
            with open(spec_path, encoding="utf-8") as f:
                spec = json.load(f)
            jobs[ex.submit(judge, png, model)] = (n, spec, png)
        for fut in futures.as_completed(jobs):
            n, spec, png = jobs[fut]
            try:
                score, why = fut.result()
            except Exception as e:
                print(f"[evolve] seed {n:03d} judge failed: {e}")
                continue
            genome = genome_from(spec)
            probe = build_spec(spec, genome, {})
            issues = resolve_palette(probe)
            if issues:
                print(f"[evolve] seed {n:03d} genome unusable: {issues[0]}")
                continue
            entry = {"id": f"seed-{n:03d}", "gen": 0, "chassis": n, "genome": genome,
                     "score": score, "why": why, "niche": niche_key(probe),
                     "png": os.path.relpath(png, ROOT)}
            entries.append(entry)
            cell = archive["cells"].get(entry["niche"])
            if not cell or score > cell["score"]:
                archive["cells"][entry["niche"]] = entry
            print(f"[evolve] seed {n:03d}: {score:.1f} ({entry['niche']}) — {why}")
    save(archive, entries)
    print(f"[evolve] archive seeded: {len(archive['cells'])} cells filled")


def cmd_gen(model):
    cfg = load_cfg()
    archive = load_archive()
    g = archive["generation"] + 1
    rng = random.Random(f"{CAMPAIGN}-gen{g:02d}")
    elites = list(archive["cells"].values())
    if not elites:
        sys.exit("[evolve] empty archive — run --init first")
    chassis_specs = {}
    for n in CHASSIS:
        with open(os.path.join(ROOT, "scenes", "pylon-series", f"{n:03d}.json"),
                  encoding="utf-8") as f:
            chassis_specs[n] = json.load(f)

    gen_dir = os.path.join(CDIR, f"gen-{g:02d}")
    out_dir = os.path.join(ODIR, f"gen-{g:02d}")
    os.makedirs(gen_dir, exist_ok=True)
    blender = os.environ.get("BLENDER", "blender")
    born = []
    for i in range(GEN_SIZE):
        for _ in range(MUTATION_TRIES):
            a = rng.choice(elites)
            genome = (crossover(a["genome"], rng.choice(elites)["genome"], rng)
                      if rng.random() < 0.5 else a["genome"])
            genome = mutate(genome, rng)
            chassis = a["chassis"]
            cid = f"c{g:02d}-{i:02d}"
            spec = build_spec(chassis_specs[chassis], genome, {
                "campaign": CAMPAIGN, "gen": g, "id": cid, "chassis": chassis,
                "parents": [a["id"]], "gates": GATE_OVERRIDES})
            issues = resolve_palette(spec) + check(spec, cfg)
            if not issues:
                path = os.path.join(gen_dir, cid + ".json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(spec, f, ensure_ascii=False, indent=2)
                born.append((cid, chassis, genome, path))
                break
    print(f"[evolve] gen {g}: {len(born)}/{GEN_SIZE} bred valid")

    rendered = []
    for cid, chassis, genome, path in born:
        png = os.path.join(out_dir, cid + ".png")
        if render(blender, path, png, draft=True):
            rendered.append((cid, chassis, genome, png))
    print(f"[evolve] gen {g}: {len(rendered)}/{len(born)} rendered")

    entries = []
    with futures.ThreadPoolExecutor(max_workers=4) as ex:
        jobs = {ex.submit(judge, png, model): (cid, chassis, genome, png)
                for cid, chassis, genome, png in rendered}
        for fut in futures.as_completed(jobs):
            cid, chassis, genome, png = jobs[fut]
            try:
                score, why = fut.result()
            except Exception as e:
                print(f"[evolve] {cid} judge failed: {e}")
                continue
            with open(os.path.join(gen_dir, cid + ".json"), encoding="utf-8") as f:
                spec = json.load(f)
            entry = {"id": cid, "gen": g, "chassis": chassis, "genome": genome,
                     "score": score, "why": why, "niche": niche_key(spec),
                     "png": os.path.relpath(png, ROOT)}
            entries.append(entry)
            cell = archive["cells"].get(entry["niche"])
            marker = ""
            if not cell or score > cell["score"]:
                archive["cells"][entry["niche"]] = entry
                marker = " *new elite*"
            print(f"[evolve] {cid}: {score:.1f} ({entry['niche']}){marker} — {why}")
    archive["generation"] = g
    save(archive, entries)
    print(f"[evolve] gen {g} done: {len(archive['cells'])} cells filled")


LIGHT_BINS = ["dark (<0.55)", "dusk (0.55-0.72)", "mid (0.72-0.88)", "pale (>0.88)"]

# Plain string, not an f-string — the braces are JavaScript's.
REPORT_JS = """
<div id="lb"><img id="big"></div>
<script>
const KEY = "CAMPAIGN-author-scores";
let scores = Object.assign({}, PRE, JSON.parse(localStorage.getItem(KEY) || "{}"));
function paint() {
  document.querySelectorAll(".sc").forEach(sc => {
    sc.querySelectorAll("button").forEach(b =>
      b.classList.toggle("on", scores[sc.dataset.id] == +b.textContent));
  });
}
document.addEventListener("click", e => {
  const b = e.target;
  if (b.matches(".sc button")) {
    scores[b.closest(".sc").dataset.id] = +b.textContent;
    localStorage.setItem(KEY, JSON.stringify(scores));
    paint();
  } else if (b.matches("img.z")) {
    document.getElementById("big").src = b.src;
    document.getElementById("lb").style.display = "flex";
  } else if (b.closest("#lb")) {
    document.getElementById("lb").style.display = "none";
  }
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape") document.getElementById("lb").style.display = "none";
});
function exportScores() {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(scores, null, 1)],
                                        {type: "application/json"}));
  a.download = "CAMPAIGN-author-scores.json";
  a.click();
}
paint();
</script>
"""


def _score_widget(eid):
    btns = "".join(f"<button>{i}</button>" for i in range(1, 11))
    return f"<div class='sc' data-id='{eid}'>{btns}</div>"


def cmd_report():
    archive = load_archive()
    _, lpath = _paths()
    log = [json.loads(line) for line in open(lpath, encoding="utf-8")]
    author_path = os.path.join(CDIR, "author.json")
    author = (json.load(open(author_path, encoding="utf-8"))
              if os.path.exists(author_path) else {})
    cells = archive["cells"]
    rows = []
    for li in range(3, -1, -1):
        for steel in ("neutral", "colored"):
            tds = []
            for h in range(6):
                e = cells.get(f"h{h}-l{li}-{steel}")
                if e:
                    png = os.path.join(ROOT, e["png"]).replace(os.sep, "/")
                    sw = oklch_to_hex(*e["genome"]["horizon"])
                    tds.append(
                        f"<td><img class='z' src='file:///{png}' width='150' "
                        f"loading='lazy'><br><b>{e['score']:.1f}</b> {e['id']} "
                        f"<span class='sw' style='background:{sw}'></span>"
                        f"{_score_widget(e['id'])}</td>")
                else:
                    tds.append("<td class='empty'>—</td>")
            rows.append(f"<tr><th>{LIGHT_BINS[li]}<br>{steel}</th>{''.join(tds)}</tr>")
    log_rows = "".join(
        f"<tr><td><img class='z' src='file:///"
        f"{os.path.join(ROOT, e['png']).replace(os.sep, '/')}' width='90' "
        f"loading='lazy'></td>"
        f"<td>{e['id']}</td><td>{e['gen']}</td><td>{e['chassis']:03d}</td>"
        f"<td>{e['score']:.1f}</td><td>{_score_widget(e['id'])}</td>"
        f"<td>{e['niche']}</td><td>{e['why']}</td></tr>"
        for e in sorted(log, key=lambda e: -e["score"]))
    hue_heads = "".join(f"<th>hue {h * 60}-{h * 60 + 60}</th>" for h in range(6))
    page = f"""<!doctype html><meta charset="utf-8">
<title>{CAMPAIGN} — niche map</title>
<style>
 body {{ background:#16181c; color:#cfd3da; font:13px/1.5 system-ui; margin:2rem; }}
 td, th {{ padding:6px; text-align:center; vertical-align:top; }}
 td.empty {{ color:#3a3f47; }}
 img {{ border-radius:3px; display:block; margin:auto; }}
 img.z {{ cursor: zoom-in; }}
 .sw {{ display:inline-block; width:.8em; height:.8em; border-radius:50%;
       vertical-align:-1px; border:1px solid #444; }}
 .sc button {{ background:#23272e; color:#8b93a0; border:0; border-radius:3px;
       margin:1px; padding:2px 5px; cursor:pointer; font-size:11px; }}
 .sc button.on {{ background:#4a9eff; color:#fff; }}
 #lb {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,.88);
       align-items:center; justify-content:center; cursor: zoom-out; z-index:9; }}
 #lb img {{ max-width:94vw; max-height:94vh; }}
 table.log td {{ text-align:left; }} h2 {{ margin-top:2rem; }}
 .bar {{ position:sticky; top:0; background:#16181c; padding:.5rem 0; }}
 .bar button {{ background:#2e7d46; color:#fff; border:0; border-radius:4px;
       padding:6px 14px; cursor:pointer; }}
</style>
<h1>{CAMPAIGN} — generation {archive['generation']}, {len(cells)} cells filled,
{len(log)} individuals evaluated</h1>
<div class="bar"><button onclick="exportScores()">내 점수 내보내기 (JSON)</button>
 점수를 매기면 브라우저에 저장됩니다 — 끝나면 내보내서
 <code>--ingest</code>로 회수하세요. 이미지 클릭 = 확대.</div>
<table><tr><th></th>{hue_heads}</tr>{''.join(rows)}</table>
<h2>all individuals, best first</h2>
<table class="log"><tr><th></th><th>id</th><th>gen</th><th>chassis</th><th>judge</th>
<th>내 점수</th><th>niche</th><th>judge says</th></tr>{log_rows}</table>
<script>const PRE = {json.dumps(author)};</script>
{REPORT_JS.replace("CAMPAIGN", CAMPAIGN)}
"""
    path = os.path.join(ODIR, "map.html")
    os.makedirs(ODIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"[evolve] {path}")
    return path


def cmd_ingest(path):
    """Author scores in: they become the authoritative fitness. Elites re-rank with
    the author's number wherever one exists, and the judge's agreement is measured —
    the proxy is only as good as that number."""
    with open(path, encoding="utf-8") as f:
        new = {k: float(v) for k, v in json.load(f).items()}
    apath = os.path.join(CDIR, "author.json")
    author = (json.load(open(apath, encoding="utf-8"))
              if os.path.exists(apath) else {})
    author.update(new)
    with open(apath, "w", encoding="utf-8") as f:
        json.dump(author, f, indent=1)

    _, lpath = _paths()
    log = [json.loads(line) for line in open(lpath, encoding="utf-8")]
    pairs = [(author[e["id"]], e["score"], e["id"]) for e in log if e["id"] in author]
    archive = load_archive()
    cells = {}
    for e in log:
        s = author.get(e["id"], e["score"])
        e2 = {**e, "score": s, "scored_by": "author" if e["id"] in author else "judge"}
        c = cells.get(e["niche"])
        if not c or s > c["score"]:
            cells[e["niche"]] = e2
    archive["cells"] = cells
    save(archive, [])
    print(f"[evolve] {len(author)} author scores in; elites re-ranked "
          f"({len(cells)} cells)")
    if pairs:
        d = sum(abs(a - j) for a, j, _ in pairs) / len(pairs)
        print(f"[evolve] judge agreement: mean |author - judge| = {d:.2f} "
              f"over {len(pairs)} shared")
        for a, j, eid in sorted(pairs, key=lambda p: -abs(p[0] - p[1]))[:3]:
            print(f"[evolve]   biggest gap: {eid} author {a:g} vs judge {j:g}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--gen", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--ingest", metavar="F.json")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()
    if args.init:
        cmd_init(args.model)
    if args.gen:
        cmd_gen(args.model)
    if args.ingest:
        cmd_ingest(args.ingest)
    if args.ingest or args.report:
        cmd_report()
    if not (args.init or args.gen or args.report or args.ingest):
        sys.exit("nothing to do — pass --init, --gen, --report, or --ingest")


if __name__ == "__main__":
    main()
