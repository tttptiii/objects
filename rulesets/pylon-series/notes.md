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

## Not yet variables (candidates for later)

Tower proportions (height, base ratio, waist independently), bracing pattern (X/V/K),
arm hierarchy, pole-type structures (single concrete pole), **line bends (angle
towers)** — deferred: bends break the straight-line assumption threaded through the
frustum check, the terrain corridor, and the wire chain, so they are a structural
revision, not a promotion. All live as constants in `pylon3d.py`, promotable one at a
time — each promotion is a rules revision with its own calibration.
