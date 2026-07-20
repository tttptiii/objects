# objects

A series of images built from nothing but rules. This is the third phase, following
2016 (#0–8) and 2017 (#9–23); numbering restarts at 0.

A picture here depicts no situation — there are only rules. The rules are formalized
as version-controlled prompts, a language model samples within them, deterministic
validators reject what breaks them, and human judgment of the results feeds back into
the next revision of the rules. **The prompts are the published artifact** — the
procedure is meant to be runnable by anyone.

The current series is **pylon-series**: a line of lattice transmission towers in fog.

| bare sky | snowfield | mountains | farmland |
|---|---|---|---|
| ![](docs/images/002-bare-sky-dawn.png) | ![](docs/images/025-snowfield-dusk.png) | ![](docs/images/028-mountains-valley.png) | ![](docs/images/039-farmland.png) |

## The division of labor

**Fixed by code** (`scripts/pylon3d.py`): the towers themselves. Real suspension-tower
proportions — 45 m body, square lattice with X-braced panels, volumetric arm trusses,
insulator strings, conductors hanging as true catenaries. Every member renders as a
continuous beveled line. A wire cannot float: its endpoints are computed from the arms
it hangs from, so placement has stakes because the object is real.

**Decided by the model, within the rules** (`rulesets/pylon-series/rules.md`): the
camera, the rhythm of the line (tower count, spans, tower scale, insulator style), the
sky and fog, the palette, and an optional landscape.

**The world is emission-only** — there are no lights. Fog, ground mist, and terrain
shading are all material arithmetic, so a palette color renders as exactly that color.
The land, when present, is deliberately unreal: a low-poly faceted heightfield, each
flat-shaded triangle taking a single tone.

## The procedure

```
rulesets/pylon-series/rules.md          the prompt — the thing being published
        │
        ▼
sample_pylons.py                        claude -p, headless; one piece per direction
        │        ◄──────────────┐
        ▼                       │ violations (retried in-session,
validate_pylon.py               │  rejects kept with reasons)
  pure python: projects the     │
  tower through the camera,     │
  walks the same terrain        │
  functions the renderer uses,  │
  checks fog and ΔE contrast ───┘
        │ pass
        ▼
scenes/pylon-series/NNN.json            the unit of reproduction
        │
        ▼
render_pylon3d.py (Blender, Cycles)     spec -> image, deterministically
        │
        ▼
human judgment ──► the next revision of rules.md
```

**The refusals are part of the record.** Rejected candidates are kept in
`scenes/pylon-series/rejected/` with the violations that killed them, and whole
directions the author rejected stay in the history. The flat cutout terrain below was
judged as adding close to nothing — the critique that led to the low-poly landscape:

| rejected direction | where it ended up |
|---|---|
| ![](docs/images/rejected-008-flat-terrain.png) | ![](docs/images/028-mountains-valley.png) |

## Reproducibility

- **Deterministic**: `scenes/*.json` → image. Rendering is a pure function of the
  spec — no lights, no denoiser, no randomness anywhere in the geometry (terrain is
  sine-sum heightfields; even the low-poly jitter is a sine hash). Every generative
  rule is a deterministic function.
- **Non-deterministic**: prompt → JSON. LLM sampling does not reproduce; instead each
  spec's `meta` records the model and a content hash of the exact rules revision
  (`rules_sha`) that produced it.

Whether a given composition can be sampled again is not guaranteed. Whether a given
image can be made again is.

## Layout

```
rulesets/pylon-series/
  rules.md      the prompt, sent verbatim — nothing else is
  checks.json   validation thresholds, layered over defaults
  notes.md      for humans: where each rule came from
scenes/         specs, the units of reproduction (rejected/ and superseded/ included)
scripts/        sampling, validation, rendering — python stdlib only
outputs/        rendered images (reproducible, not tracked)
docs/images/    curated copies for this page
```

## Setup and running

Python 3.11+ (stdlib only), Blender 4.x+, and the claude CLI (logged in — no API key).

```sh
# sample pieces (direction is fixed per piece number; rejects are kept)
python scripts/sample_pylons.py --only 24,25,26,27

# render drafts / full resolution (set BLENDER, or have blender on PATH)
python scripts/render_pylons.py --pieces 24-27
python scripts/render_pylons.py --pieces 26 --full

# render the hand-authored demo scenes
python scripts/render_pylons.py --demos
```

## License

Undecided — to be settled before the first public release. Likely split between code
and images (MIT for one, CC BY-NC for the other).
