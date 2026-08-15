# pylon-series — notes

`rules.md` is the prompt, sent to the model verbatim. Reasoning and history live here.

- status: draft
- engine: pylon3d (parametric 3D geometry + beveled-curve renderer)

## Where this came from

The abstract placement grammar kept producing arrangements the author read as meaningless
— rule combinations with nothing at stake. The author's counter-proposal: restrict the
element pool to something specific, and specifically to **real transmission towers** —
thin linear structures with gravity-sagged catenary wires. Demos built from researched
proportions (45 m suspension tower, 7 m base, square lattice, three arm levels,
double-circuit) landed immediately — the author's first wholehearted approval in the
project.

The vocabulary solves the meaninglessness structurally. A wire cannot float — its
endpoints are computed from the arm tips it hangs from. Placement has stakes because the
object is real.

## The division of labor

**Fixed by code** (`pylon3d.py`): tower geometry — taper, panels, X-bracing, volumetric
arm trusses, insulators, catenaries (true cosh). Members render as beveled curves —
continuous lines, never chains of dots (bead-chain wires were explicitly rejected).

**Decided by the model** (this rule set): camera (position, aim, lens), line rhythm
(tower count, span), sky palette (OKLCH horizon/zenith + cloud mottle), steel color, fog
(`fog_clear`, `fog_depth`).

## Rules with a history

- **Always looking up.** The frontal flat view was rejected; the series is the worm's-eye
  three-point perspective. Camera z 0.4–10, aim z ≥ 18.
- **The subject is sharp; fog belongs to the background.** The first fog implementation
  faded everything with distance, softening the subject's own peak — rejected: the
  pylon itself must stay sharp. `fog_clear` keeps a zone untouched, and the validator requires the
  nearest tower's peak inside it.
- **Fog, not weather drama.** Horizon and zenith must stay within ΔE 0.14 — a hazy
  deepening, never a sunset gradient. Clouds are a low-contrast mottle.
- **Steel stands against the sky**: ΔE ≥ 0.35 from the horizon.
- **The frustum check.** The model aims a camera it cannot look through, so the validator
  projects the nearest tower's keypoints (base corners, waist corners, arm tips, peak)
  through the specified camera: at least 5 in frame, at least one above the waist.
  Verified to reject cameras aimed away, at the ground, or from too high.

- **Terrain, second attempt (2026-07-25).** The first terrain (flat cutout ridges +
  ground plane + treeline, pieces 008–015) was judged by the author as adding close
  to nothing. Root cause: this is an emission-only world, so a
  silhouette has no interior; relief without shading is a flat stripe. Replaced with two
  landscape kinds built as real displaced heightfield meshes (deterministic sine-sum
  field — the validator evaluates the same function for tower base heights and the
  camera-above-ground check) plus **normal-based slope shading in the material**, the
  no-sun substitute for shadows. `snowfield` per the author's direction: ground nearly
  one with the sky, only drift shadows reading. `mountains` per the author's direction:
  the towers far off (150–900 m) and ranges layered — the vista, with a wider camera
  envelope and deeper fog. Farmland dropped for now — no idea strong enough yet.
  Calibration findings from the first sampled batch (pieces 024–031): models aimed
  snow cameras so steeply the field left the frame (→ bottom-of-frame-reaches-ground
  check, 60 m); one model omitted the terrain block on a snowfield direction (→
  "direction names a landscape ⇒ terrain required"); the fog clear zone on the flat
  valley floor drew a hard onset line (→ mountains fade from the camera, snow keeps
  the clear zone); the lead half-span wires hung cut in mid-air at vista distances
  (→ `lead=False` for mountains); and models copied the example phase, repeating the
  same landscape (→ "examples are shapes, not answers").

- **Farmland, from a reference (2026-07-31).** The author supplied a reference
  photograph (`ref_pylon/cap1.png`, kept out of the public repo): dawn over English
  crop country. Decoded into a third terrain kind: crop field with a two-tone noise
  mottle, a hedgerow curtain mesh (nearly continuous lumpy band, slow height swell),
  **ground mist pooled behind the hedge** (height-based fog: whitens what stands low
  and far — hills and the far towers' legs rise out of it, applied to the steel
  material too), low rolling hills well behind, and the one sky allowed a dawn
  gradient (warm horizon under cool zenith, ΔE ≤ 0.30). Composition reuses the
  mountains anchor-and-layers rules. Calibration: models hid the land (steep aims)
  and pushed the hedge to a pencil line at 350 m → ground-reach check (80 m) and
  hedge 70–200 m in front of the camera. Lessons: a displaced sheet reads as a
  ribbon — the hedge needed a vertical curtain with a face toward the camera; mist
  must gate past the hedge's camera distance, not the world coordinate.

- **The low-poly pivot (2026-08-02).** The texture road (noise mottle, then wave-node
  crop rows after the reference photo) was judged by the author too artificial —
  worse, not better. The author proposed the opposite direction: an openly unreal landscape
  of poly surfaces. This fits the pipeline's nature — an emission-only world has no
  photographic ground to imitate, but flat-shaded facets each take a single clean
  tone. Terrain meshes became jittered triangulated grids (`tri_mesh`, deterministic
  sine-hash jitter and diagonal choice; `facet` = triangle size in meters, a spec
  variable), flat shading forced explicitly (Blender 5.x builds smooth meshes from
  from_pydata). Two material moves make the style: a tight slope-shading window
  (gentle facet tilts must spread into distinct tone steps) and **per-facet tone
  jitter** — the facet normal, constant per face under flat shading, hashed through
  white noise gives every facet its own fixed offset. Crop rows/mottle removed.

- **Containment (2026-08-02).** With the low-poly direction approved, the author's
  remaining ask was framing: the tower must sit well inside the picture. Landscape
  pieces now require the nearest tower wholly in frame (all 15 keypoints, 6% margin)
  and within a height-share band (anchor 0.25–0.85, snow 0.35–0.9); the looming crop
  stays exclusive to the bare-sky close-ups. Two infrastructure bugs surfaced while
  enforcing this: (1) direction assignment by `piece % len(DIRECTIONS)` silently
  reshuffled every piece each time the list grew — replaced with a frozen explicit
  table (`direction_index`); (2) prose could not force the terrain block, so the
  sampler now hard-requires the direction's terrain kind in validation.

- **Variables and the palette grammar (2026-08-02).** Three constants promoted to
  spec variables: `tower_scale` (0.85–1.2 — light rural line vs heavy trunk line),
  `insulators` "I"/"V" (v-string pairs spread across the arms), and `span` as a
  per-gap list (the rhythm of the line). The 2D-era color-relation vocabulary
  (colorutil `derive`: muted/tint/shade/analogous/complement/triad/neutral) became
  the optional `palette` block — one anchor (the fog color) plus named moves,
  resolved deterministically before validation, so a palette is coherent by
  construction and still faces every contrast gate. Also: the 2D canvas pipeline was
  deleted outright (author's call), and every sampled spec now records `rules_sha`,
  the content hash of the exact rules revision that produced it.

- **The design space and the visible loop (2026-07-26).** Two scaffolding moves.
  First, directions left the code: per-piece briefs now live in `directions.json`,
  where the 20 existing pieces keep their hand-written briefs forever and every new
  piece number is a point in a declared design space — stage × mood × density ×
  composition × line, 960 combinations walked in a decorrelated order. The mood axis
  bounds each piece's palette anchor (hue and lightness bands, enforced at sampling
  time), which turns batch-level color spread from luck into a rule. Second, the
  judgment loop became visible in the repo: `judgments/` records the author's
  per-batch verdicts (the third arrow of rules → pieces → judgment → rules), and
  `scripts/report_pylons.py` generates a piece manifest (`docs/pieces.md`) and a
  local contact sheet. First design-space pieces (040–043) produced combinations the
  hand-written list never reached — a violet-evening snowfield, a chroma-less winter
  mountain line, a blue-dusk farmland seen from a flank.

- **The graphic identity, confirmed (2026-08-11).** The author weighed redirecting
  the series toward photorealism (sun lighting, physical fog scattering, camera
  artifacts — each of which would break the emission-only invariant and the exact-
  palette guarantee) and confirmed the graphic identity instead: emission-only,
  colors rendering as exactly themselves, and the low-poly land are definitional,
  not provisional. Realism is out of scope. Publishing decisions that followed:
  the frame stays square (a 4:5 portrait axis for Instagram was considered and
  deferred — it would recalibrate every containment and anchor-frac threshold),
  the license split was settled (MIT for code, CC BY-NC 4.0 for images), and
  `scripts/export_instagram.py` packages full renders with captions built from
  each piece's brief. Open observations for the next batch's judgment: head-on
  compositions (e.g. 055) show the jumper catenaries tangled in front of the body
  when seen edge-on; the farmland horizon (e.g. 039) can read as a hard dark band
  where the hills meet the field.

- **The color campaign, distilled (2026-08-12).** The first evolution campaign
  (`scripts/evolve_colors.py`, `scenes/evolve/color-01/`): palette genomes evolved
  MAP-Elites-style on frozen composition chassis — 3 generations, 68 individuals,
  a vision judge scoring drafts against the author's recorded taste, the author's
  own 1–10 scores overriding it (mean |author − judge| ≈ 0.8; the judge's one
  exposed bias: it undervalued quiet near-grayscale pieces the author rates
  highly). The map's verdict: the fertile ground is the dusk-dark territory the
  mood bands forbade (anchor lightness below 0.72), plus gentle two-tone
  gradients; grayscale drift, neon, and mismatched two-palette pieces are dead
  zones; colored steel stays unproven. Distilled into the rules: pieces 040–083
  frozen into `assigned` first (axis growth reshuffles the walk — same bug class
  as the 2026-07-26 `direction_index` fix), then four mood bands added (teal-dusk,
  slate-dusk, amber-dusk, ashen — boundaries taken from the elites' actual
  anchors, not the judge's color words), and the sunset cap eased 0.14 → 0.22.
  The design space is now 1,728 combinations; the series darkens from piece 084 on.

- **The farmland envelope the prompt never mentioned (2026-08-13).** `validate_pylon`
  has always given farmland the mountains fog and camera envelope — `fog_clear`
  60–400, `fog_depth` 200–900, camera z to 40 — because the kind is built out of
  distance. `rules.md` stated those numbers only in the mountains section, and the
  farmland section's list of what carries over named the anchor, the tower count, the
  dissolve, and the ground reach, but not fog. So the model used the general
  envelope and was refused for it: **every farmland piece sampled to date — 051, 055,
  059, 067, 079, 083, 087, 091 — failed its first attempt on `fog_clear` or
  `fog_depth`.** Not one mountains piece ever did, because mountains was documented.
  A gate the prompt does not describe is a retry tax, not a rule; the sentence was
  added to the farmland section and nothing about what is permitted changed.

- **The line has no ends (2026-08-13).** Conductors used to continue a span past the
  first tower toward the camera (`lead`) and simply stop at the last one, so every
  picture quietly claimed the line began somewhere off-frame and terminated in view.
  A transmission line does neither. The renderer now continues both ends by one span
  (`trail`, symmetric with `lead`), and the line is cut by the frame and the fog
  instead of by the geometry. The cut end at the far side is not guarded: 32 of 73
  pieces put it inside the frustum, but at 350–700 m a conductor is a hairline the
  colour of the haze, and 082 — the worst case at 0.32 dissolve — shows nothing at
  full resolution. `lead` needed its guard because its cut end is near, thick, and
  dark; this one does not. Geometry unchanged, so specs and thresholds are untouched;
  every render changes. All 73 pieces were re-rendered at both sizes and the hosted
  JPEGs replaced, so the queue serves the current geometry. **000–003 had already gone
  out and keep the old one** — Instagram copies an image when it publishes, and the
  feed is not rewritten. The first four pictures in the series are the ones where the
  line still ends. That is a fact about the series now, not a defect to paper over.

## Not yet variables (candidates for later)

Tower proportions (height, base ratio, waist independently), bracing pattern (X/V/K),
arm hierarchy, pole-type structures (single concrete pole), **line bends (angle
towers)** — deferred: bends break the straight-line assumption threaded through the
frustum check, the terrain corridor, and the wire chain, so they are a structural
revision, not a promotion. All live as constants in `pylon3d.py`, promotable one at a
time — each promotion is a rules revision with its own calibration.
