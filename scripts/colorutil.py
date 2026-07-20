"""OKLCH -> sRGB. The canonical color space for palettes is OKLCH (perceptually uniform).

Blender materials take linear sRGB and card composition (PIL) takes gamma-encoded 8-bit
sRGB, so both are provided. Colors outside gamut are simply clamped.
"""

import math

MAX_CHROMA = 0.4
DEFAULT_MUTE = 0.55      # how much chroma `muted` retains
DEFAULT_STEP = 0.18      # lightness shift for tint/shade

RELATIONS = {"muted", "tint", "shade", "analogous", "complement", "triad", "neutral"}

# `amount` means something different per relation, which the model gets wrong often enough
# to matter (measured: 4 of 10 palettes in one batch were thrown out of range this way).
AMOUNT_RANGES = {
    "muted": (0.0, 1.0, "fraction of chroma retained"),
    "tint": (0.0, 1.0, "lightness shift"),
    "shade": (0.0, 1.0, "lightness shift"),
    "neutral": (0.0, 1.0, "target lightness"),
    "analogous": (-180.0, 180.0, "hue shift in degrees"),
    "triad": (-1.0, 1.0, "direction"),
}


def oklch_range_issues(label, oklch):
    """Whether an OKLCH triple is in range. Out-of-range values get clamped downstream and
    render as a color nobody chose."""
    if not (isinstance(oklch, (list, tuple)) and len(oklch) == 3):
        return [f"{label}.oklch must be three values [L, C, H]"]
    L, C, H = oklch
    out = []
    if not 0 <= L <= 1:
        out.append(f"{label}: L={L} out of range — lightness is 0-1 (0.55, not 55)")
    if not 0 <= C <= MAX_CHROMA:
        out.append(f"{label}: C={C} out of range — chroma is 0-{MAX_CHROMA} and never negative")
    if not 0 <= H <= 360:
        out.append(f"{label}: H={H} out of range — hue is 0-360")
    return out


def amount_issues(label, relation, amount):
    """Whether `amount` fits its relation. The relation name sets the direction, so a negative
    amount cannot flip it — it only pushes the value out of range."""
    rng = AMOUNT_RANGES.get(relation)
    if not rng or not isinstance(amount, (int, float)):
        return []
    lo, hi, meaning = rng
    if lo <= amount <= hi:
        return []
    return [
        f"{label} ({relation}): amount={amount} out of range — it is the {meaning} "
        f"and must be {lo}-{hi}. The relation name sets the direction, so a negative amount "
        f"cannot make '{relation}' act in reverse"
    ]


def oklch_to_linear_srgb(L, C, H):
    """OKLCH -> linear sRGB, each channel clamped to 0..1. H in degrees."""
    h = math.radians(H)
    a = C * math.cos(h)
    b = C * math.sin(h)

    # OKLab -> LMS' (Bjorn Ottosson)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b

    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3

    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s

    clamp = lambda x: min(1.0, max(0.0, x))
    return clamp(r), clamp(g), clamp(bl)


def _encode(c):
    """linear -> sRGB gamma encoding."""
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * (c ** (1 / 2.4)) - 0.055


def oklch_to_srgb8(L, C, H):
    """OKLCH -> gamma-encoded 8-bit (r, g, b)."""
    lin = oklch_to_linear_srgb(L, C, H)
    return tuple(round(_encode(c) * 255) for c in lin)


def oklch_to_hex(L, C, H):
    return "#%02x%02x%02x" % oklch_to_srgb8(L, C, H)


def clamp_oklch(L, C, H):
    """Force OKLCH into range. Out-of-range values reaching the renderer produce the wrong color."""
    return [min(1.0, max(0.0, L)), min(MAX_CHROMA, max(0.0, C)), H % 360.0]


def derive(primary, relation, amount=None):
    """Derive a secondary from the primary by relation — the unit of color experiment.

    Holding the composition and varying only the relation isolates what each kind of
    supporting color does. `primary` is [L, C, H].

    **The relation name sets the direction; `amount` only sets the magnitude.** Sign is
    therefore ignored — this prevents `shade` with -25 from pushing lightness to 25.65
    (measured: 4 of 10 palettes in one batch had colors thrown out of range this way).

    - muted      : same hue, lower chroma (amount = fraction of chroma kept, 0..1, default 0.55)
    - tint/shade : same hue, lightness up or down (amount = shift, 0..1, default 0.18)
    - analogous  : neighbor on the hue wheel (amount = degrees; sign means direction here)
    - complement : opposite hue (+180)
    - triad      : a third of the wheel (amount = ±1 for direction)
    - neutral    : chroma stripped away (amount = target lightness, 0..1, default 0.5)
    """
    L, C, H = primary
    mag = None if amount is None else abs(float(amount))
    if relation == "muted":
        ratio = DEFAULT_MUTE if mag is None else min(1.0, mag)
        return clamp_oklch(L, C * ratio, H)
    if relation == "tint":
        return clamp_oklch(L + (DEFAULT_STEP if mag is None else min(1.0, mag)), C * 0.7, H)
    if relation == "shade":
        return clamp_oklch(L - (DEFAULT_STEP if mag is None else min(1.0, mag)), C * 0.8, H)
    if relation == "analogous":
        return clamp_oklch(L, C, H + (30.0 if amount is None else float(amount)))
    if relation == "complement":
        return clamp_oklch(L, C, H + 180.0)
    if relation == "triad":
        return clamp_oklch(L, C, H + 120.0 * (1 if amount is None else (1 if amount >= 0 else -1)))
    if relation == "neutral":
        return clamp_oklch(0.5 if mag is None else min(1.0, mag), 0.01, H)
    raise ValueError(f"unknown color relation: {relation}")


def oklch_to_oklab(L, C, H):
    h = math.radians(H)
    return (L, C * math.cos(h), C * math.sin(h))


def delta_e(c1, c2):
    """Perceptual distance between two OKLCH colors (Euclidean in Oklab).

    OKLCH is perceptually uniform, so this distance corresponds directly to how different
    two colors look. Comparing lightness alone misses cases like a white shape on a light
    gray ground (ΔL ≈ 0.02); including chroma and hue catches them.
    """
    a, b = oklch_to_oklab(*c1), oklch_to_oklab(*c2)
    return math.dist(a, b)
