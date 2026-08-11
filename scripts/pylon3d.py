"""A real lattice transmission tower, as parametric geometry. Pure Python — no bpy.

Proportions follow actual suspension towers (double-circuit, square lattice, the Eurasian
type): ~45 m tall on a ~7 m square base, four corner legs tapering to a waist, X-braced
panels on every face, three cross-arm levels per side, suspension insulator strings hanging
from the arm tips, and conductors following true catenaries (cosh) to the next tower.

Everything is emitted as line segments and polylines in meters, Z up:

    members : [(p0, p3), ...] grouped by thickness class — "leg", "brace", "arm"
    wires   : [ [p, p, ...], ... ] sampled catenaries
    insulators : [(top, bottom), ...]

The renderer turns members into thin beveled curves — actual lines, not chains of dots.
"""

import math

# Real-ish proportions (meters)
HEIGHT = 45.0            # body top (peak adds more)
BASE_HALF = 3.5          # half-width of the square base -> 7 m base
WAIST_HALF = 1.05        # half-width at the first arm level
TOP_HALF = 0.85          # half-width at body top
WAIST_Z = 26.0           # where the taper eases off
PANELS_BELOW = 7         # lattice panels base..waist
PANELS_ABOVE = 5         # waist..top
ARM_LEVELS = (28.0, 34.5, 41.0)     # cross-arm heights
ARM_REACH = (6.8, 5.9, 5.0)         # tip distance from axis, longest at the bottom
ARM_DROP = 2.2           # vertical depth of the arm truss at the body
PEAK_H = 4.5             # peak above body top (carries the ground wire)
INSULATOR_LEN = 3.2      # suspension string length
SPAN = 120.0             # distance to the neighbouring towers
SAG = 4.2                # conductor sag over one span
GROUND_SAG = 2.6


# Terrain is a deterministic heightfield — no randomness, per the project rule. Octaves
# of plane waves in golden-angle directions; `phase` decorrelates one landscape from
# another. Being a pure function of position, the validator can evaluate the exact same
# ground the renderer builds (tower base heights, camera-above-ground).
_FIELD_OCTAVES = 5
_FIELD_DIRS = 3
_GOLD = 2.399963229728653       # golden angle — wave directions never align

MOUNTAIN_DEFAULTS = {"relief": 260.0, "scale": 900.0, "phase": 0.0, "sharp": 0.6}
SNOW_DEFAULTS = {"relief": 2.0, "scale": 70.0, "phase": 0.0}


def _clamp01(t):
    return 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)


def _smooth(t):
    t = _clamp01(t)
    return t * t * (3.0 - 2.0 * t)


def field_height(x, y, scale, phase=0.0):
    """Normalized relief in -1..1. `scale` is the dominant feature size in meters."""
    h, norm, amp = 0.0, 0.0, 1.0
    freq = 2.0 * math.pi / max(scale, 1e-6)
    for o in range(_FIELD_OCTAVES):
        for d in range(_FIELD_DIRS):
            k = o * _FIELD_DIRS + d
            ang = phase * 1.7 + k * _GOLD
            h += amp * math.sin((x * math.cos(ang) + y * math.sin(ang)) * freq
                                + phase + k * 7.13)
            norm += amp
        amp *= 0.55
        freq *= 1.93
    # soft-limit to -1..1 — a hard clamp would flatten mountain tops into plateaus
    return math.tanh(h / (0.32 * norm))


def mountain_height(x, y, relief, scale, phase=0.0, sharp=0.6):
    """Mountain country: peaks where the field crests, plains where it dips. A valley
    corridor along the line (x near 0) keeps the towers' sightline open, and the
    foreground (small y) stays gentle so the camera region is walkable. `sharp` pushes
    crests toward peaks."""
    p = max(0.0, field_height(x, y, scale, phase)) ** (1.0 + 1.5 * sharp)
    corridor = 0.15 + 0.85 * _smooth((abs(x) - 80.0) / 420.0)
    # the foreground keeps a whisper of relief — a perfectly flat near ground reads
    # as a dead slab, and its horizon as a ruled line
    fore = 0.05 + 0.95 * _smooth((y + 120.0) / 420.0)
    return relief * p * corridor * fore


def snow_height(x, y, relief, scale, phase=0.0, wells=None):
    """Snowfield: low drifts and nothing else. The relief exists so the material's slope
    shading can hint at it — the ground itself sits a breath from the sky color.

    `wells` are tower base centers: the snow settles low around the legs (scoured and
    trodden), so the base lattice meets a consistent surface instead of being chopped
    at four different drift heights."""
    h = relief * 0.5 * (1.0 + field_height(x, y, scale, phase))
    if wells:
        near = min(math.hypot(x - wx, y - wy) for wx, wy in wells)
        h *= 0.15 + 0.85 * _smooth((near - 5.0) / 9.0)
    return h


def farmland_height(x, y, phase=0.0, hills_relief=70.0, hills_scale=800.0,
                    hedge_distance=240.0):
    """Farmland: a nearly flat crop field with a gentle ripple, low rolling hills
    rising beyond the hedgerow line. The corridor damping is mild — the hills are low
    enough that the line usually clears them anyway."""
    # rolling enough that the low-poly facets catch distinct tones
    ripple = 1.6 * (1.0 + field_height(x, y, 90.0, phase + 0.7))
    # hills rise well behind the hedge — the misty flats between them hold the mist
    rise = _smooth((y - hedge_distance - 150.0) / 600.0)
    hills = max(0.0, field_height(x, y, hills_scale, phase)) ** 1.4
    corridor = 0.5 + 0.5 * _smooth((abs(x) - 80.0) / 420.0)
    return ripple + hills_relief * hills * corridor * rise


def hedge_profile(x, height, phase=0.0):
    """The hedgerow: a nearly continuous lumpy band — bushes and the odd tree — with
    occasional real gaps, never a row of boulders."""
    c = field_height(x, 0.0, 26.0, phase + 3.3)              # clumping
    big = 0.85 + 0.35 * field_height(x, 0.0, 140.0, phase + 5.1)  # slow height swell
    return height * big * (0.55 + 0.45 * max(0.0, c + 0.75) / 1.75)


def farm_args(terrain):
    hills = terrain.get("hills") or {}
    hedge = terrain.get("hedge") or {}
    return (terrain.get("phase", 0.0), hills.get("relief", 70.0),
            hills.get("scale", 800.0), hedge.get("distance", 240.0))


def hedge_mesh(distance, height, phase=0.0, half_width=840.0, nx=210, rows=3,
               base_at=None):
    """The hedgerow as a vertical curtain with a belly — the camera sees its face, not
    the top edge of a displaced sheet. Coarse segments keep it as low-poly as the
    land. `base_at(x, y)` seats the curtain on a terrain height (default: flat 0 —
    the farmland hedge). Returns (verts, faces)."""
    verts = []
    for j in range(rows + 1):
        t = j / rows
        for i in range(nx + 1):
            x = -half_width + 2.0 * half_width * i / nx
            h = hedge_profile(x, height, phase)
            y = distance - 1.1 + 2.2 * math.sin(math.pi * t)
            base = base_at(x, y) if base_at else 0.0
            verts.append((x, y, base + h * t))
    faces = [(j * (nx + 1) + i, j * (nx + 1) + i + 1,
              (j + 1) * (nx + 1) + i + 1, (j + 1) * (nx + 1) + i)
             for j in range(rows) for i in range(nx)]
    return verts, faces


def _mountain_args(terrain):
    p = {**MOUNTAIN_DEFAULTS, **{k: terrain[k] for k in MOUNTAIN_DEFAULTS if k in terrain}}
    return p["relief"], p["scale"], p["phase"], p["sharp"]


def tower_elevations(terrain, count, span):
    """Each tower's base height comes from the land, not the spec — the wires follow."""
    ys = tower_ys(count, span)
    if terrain and terrain.get("kind") == "mountains":
        r, s, ph, sh = _mountain_args(terrain)
        return [mountain_height(0.0, y, r, s, ph, sh) for y in ys]
    if terrain and terrain.get("kind") == "farmland":
        ph, hr, hs, hd = farm_args(terrain)
        return [farmland_height(0.0, y, ph, hr, hs, hd) for y in ys]
    return None


def terrain_height_at(terrain, x, y, count=None, span=None):
    """Ground height under an arbitrary point — used to build the mesh and to keep the
    camera above ground. `count`/`span` locate the tower bases for the snow wells."""
    if not terrain:
        return 0.0
    if terrain.get("kind") == "mountains":
        return mountain_height(x, y, *_mountain_args(terrain))
    if terrain.get("kind") == "snowfield":
        p = {**SNOW_DEFAULTS, **{k: terrain[k] for k in SNOW_DEFAULTS if k in terrain}}
        wells = ([(0.0, ty) for ty in tower_ys(count, span)]
                 if count and span else None)
        return snow_height(x, y, p["relief"], p["scale"], p["phase"], wells)
    if terrain.get("kind") == "farmland":
        return farmland_height(x, y, *farm_args(terrain))
    return 0.0


def _jitter(i, j, k):
    """Deterministic pseudo-jitter in -0.5..0.5 — sine hash, no randomness."""
    return math.sin(i * 127.1 + j * 311.7 + k * 74.7) % 1.0 - 0.5


def tri_mesh(x0, x1, y0, y1, facet, height, jitter=0.42):
    """A low-poly landscape: a jittered grid split into triangles with deterministic
    diagonal choices — an irregular faceted surface. Rendered flat-shaded, each facet
    takes its own tone from the slope shading; the facets themselves are the texture.
    `facet` is the approximate triangle size in meters."""
    nx = max(2, int((x1 - x0) / facet))
    ny = max(2, int((y1 - y0) / facet))
    dx, dy = (x1 - x0) / nx, (y1 - y0) / ny
    verts = []
    for j in range(ny + 1):
        for i in range(nx + 1):
            jx = _jitter(i, j, 1) * jitter * dx if 0 < i < nx else 0.0
            jy = _jitter(i, j, 2) * jitter * dy if 0 < j < ny else 0.0
            x, y = x0 + i * dx + jx, y0 + j * dy + jy
            verts.append((x, y, height(x, y)))
    faces = []
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            b, c, d = a + 1, a + nx + 2, a + nx + 1
            if _jitter(i, j, 3) > 0.0:
                faces.extend([(a, b, c), (a, c, d)])
            else:
                faces.extend([(a, b, d), (b, c, d)])
    return verts, faces


def half_at(z):
    """Half-width of the square body at height z — strong taper to the waist, gentle above."""
    if z <= WAIST_Z:
        t = z / WAIST_Z
        return BASE_HALF + (WAIST_HALF - BASE_HALF) * t
    t = (z - WAIST_Z) / (HEIGHT - WAIST_Z)
    return WAIST_HALF + (TOP_HALF - WAIST_HALF) * t


def _corners(z):
    w = half_at(z)
    return [(-w, -w, z), (w, -w, z), (w, w, z), (-w, w, z)]


def catenary3d(p0, p1, sag, samples=48, tension=2.3):
    """A cable hanging between two points in 3D — drop applied to Z."""
    denom = math.cosh(tension) - 1.0
    out = []
    for i in range(samples):
        t = i / (samples - 1)
        drop = (math.cosh(tension) - math.cosh(tension * (2 * t - 1))) / denom
        out.append((p0[0] + (p1[0] - p0[0]) * t,
                    p0[1] + (p1[1] - p0[1]) * t,
                    p0[2] + (p1[2] - p0[2]) * t - sag * drop))
    return out


def tower(origin_y=0.0, origin_z=0.0, scale=1.0, v_strings=False):
    """One tower -> {"legs": [...], "braces": [...], "arms": [...], "insulators": [...],
    "attach": {(level, side): point}}. origin_z raises the base — a line climbing a
    slope. `scale` sizes the whole tower (light farm-country line vs heavy trunk
    line); `v_strings` hangs V-shaped insulator pairs instead of single I-strings."""
    legs, braces, arms, insulators = [], [], [], []

    def T(p):
        return (p[0] * scale, p[1] * scale + origin_y, p[2] * scale + origin_z)

    # Panel heights — denser toward the base, like the real bolted panels
    zs = [WAIST_Z * i / PANELS_BELOW for i in range(PANELS_BELOW + 1)]
    zs += [WAIST_Z + (HEIGHT - WAIST_Z) * i / PANELS_ABOVE for i in range(1, PANELS_ABOVE + 1)]

    for k in range(len(zs) - 1):
        lo, hi = _corners(zs[k]), _corners(zs[k + 1])
        for c in range(4):
            legs.append((T(lo[c]), T(hi[c])))                       # corner posts
        for c in range(4):
            d = (c + 1) % 4
            braces.append((T(lo[c]), T(hi[d])))                     # X bracing, each face
            braces.append((T(lo[d]), T(hi[c])))
            braces.append((T(hi[c]), T(hi[d])))                     # horizontal belt

    # Peak — four corner members meeting at the apex, carrying the ground wire
    top = _corners(HEIGHT)
    apex = T((0.0, 0.0, HEIGHT + PEAK_H))
    for c in top:
        legs.append((T(c), apex))

    def lerp3(a, b, t):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
                a[2] + (b[2] - a[2]) * t)

    attach = {}
    for (z, reach) in zip(ARM_LEVELS, ARM_REACH):
        w_hi, w_lo = half_at(z), half_at(z - ARM_DROP)
        d_hi, d_lo = w_hi * 0.85, w_lo * 0.55
        for side in (-1, 1):
            # A real arm is a volume: two top chords (front and back) and two bottom
            # chords converge on a short vertical tip edge, with lattice between.
            tip_t = T((side * reach, 0.0, z - 0.15))
            tip_b = T((side * reach, 0.0, z - 0.95))
            rt_f, rt_b = T((side * w_hi, -d_hi, z)), T((side * w_hi, d_hi, z))
            rb_f = T((side * w_lo, -d_lo, z - ARM_DROP))
            rb_b = T((side * w_lo, d_lo, z - ARM_DROP))

            arms += [(rt_f, tip_t), (rt_b, tip_t),                  # top chords
                     (rb_f, tip_b), (rb_b, tip_b),                  # bottom chords
                     (tip_t, tip_b),                                # tip edge
                     (rt_f, rt_b), (rb_f, rb_b),                    # root belts
                     (rt_f, rb_f), (rt_b, rb_b)]                    # root verticals
            for t in (0.35, 0.68):                                  # lattice panels
                tf, tb = lerp3(rt_f, tip_t, t), lerp3(rt_b, tip_t, t)
                bf, bb = lerp3(rb_f, tip_b, t), lerp3(rb_b, tip_b, t)
                arms += [(tf, bf), (tb, bb),                        # face verticals
                         (tf, tb), (bf, bb)]                        # top/bottom rungs
            arms += [(lerp3(rt_f, tip_t, 0.35), lerp3(rb_f, tip_b, 0.68)),
                     (lerp3(rt_b, tip_t, 0.35), lerp3(rb_b, tip_b, 0.68))]  # diagonals

            if v_strings:
                # a V restrains swing: two legs spread across the line, meeting below
                a2 = T((side * (reach - 1.5), 0.0, z - 0.95))
                v_bot = T((side * (reach - 0.75), 0.0, z - 0.95 - 2.7))
                insulators += [(tip_b, v_bot), (a2, v_bot)]
                attach[(z, side)] = v_bot
            else:
                ins_bot = (tip_b[0], tip_b[1], tip_b[2] - INSULATOR_LEN * scale)
                insulators.append((tip_b, ins_bot))
                attach[(z, side)] = ins_bot
    attach["peak"] = apex
    return {"legs": legs, "braces": braces, "arms": arms,
            "insulators": insulators, "attach": attach}


def tower_ys(count, span):
    """Tower base positions along +Y. `span` is one number or a per-gap list — the
    rhythm of the line."""
    if isinstance(span, (list, tuple)):
        gaps = list(span) + [span[-1] if span else SPAN] * max(0, count - 1 - len(span))
        ys = [0.0]
        for g in gaps[:count - 1]:
            ys.append(ys[-1] + g)
        return ys
    return [i * span for i in range(count)]


def line_of_towers(count=3, span=SPAN, elevations=None, lead=True, scale=1.0,
                   v_strings=False):
    """A run of identical towers receding along +Y, conductors and ground wire between
    them. With `lead`, half-span wires continue past the first tower toward the camera
    region — right for the worm's-eye close-up, wrong for a distant vista where their
    cut ends would hang visibly in the air. `elevations` raises each tower's base — the
    wires follow, since attachments are computed per tower."""
    elev = list(elevations or [])
    elev += [elev[-1] if elev else 0.0] * (count - len(elev))
    ys = tower_ys(count, span)
    lead_gap = span[0] if isinstance(span, (list, tuple)) else span
    towers = [tower(origin_y=ys[i], origin_z=elev[i], scale=scale,
                    v_strings=v_strings) for i in range(count)]
    wires = []

    for (z, _reach) in zip(ARM_LEVELS, ARM_REACH):
        for side in (-1, 1):
            for i in range(count - 1):
                a = towers[i]["attach"][(z, side)]
                b = towers[i + 1]["attach"][(z, side)]
                wires.append(catenary3d(a, b, SAG))
            if lead:
                first = towers[0]["attach"][(z, side)]
                wires.append(catenary3d((first[0], first[1] - lead_gap, first[2]),
                                        first, SAG))
    # ground wire along the peaks
    for i in range(count - 1):
        a, b = towers[i]["attach"]["peak"], towers[i + 1]["attach"]["peak"]
        wires.append(catenary3d(a, b, GROUND_SAG))
    if lead:
        first = towers[0]["attach"]["peak"]
        wires.append(catenary3d((first[0], first[1] - lead_gap, first[2]), first,
                                GROUND_SAG))

    merged = {"legs": [], "braces": [], "arms": [], "insulators": []}
    for t in towers:
        for k in merged:
            merged[k].extend(t[k])
    merged["wires"] = wires
    return merged
