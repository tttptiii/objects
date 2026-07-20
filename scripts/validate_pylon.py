"""Validate a pylon-series spec — pure Python, no Blender.

The model aims a camera it cannot look through, so the critical checks are geometric:
the nearest tower's keypoints are projected through the specified camera (enough in
frame; wholly contained, for landscape pieces), the camera stands above the terrain
(evaluated with the same height functions the renderer uses), and the fog leaves the
subject sharp while keeping the far line legible. Everything else is ranges and
color-contrast rules (ΔE in OKLCH).
"""

import math

import pylon3d
from colorutil import delta_e, oklch_range_issues


def keypoints(origin_y, origin_z=0.0, scale=1.0):
    """The points that decide whether a tower is 'in the picture': base corners, waist
    corners, arm tips, peak."""
    pts = []
    b = pylon3d.BASE_HALF * scale
    for (x, y) in [(-b, -b), (b, -b), (b, b), (-b, b)]:
        pts.append((x, y + origin_y, origin_z))
    w = pylon3d.half_at(pylon3d.WAIST_Z) * scale
    for (x, y) in [(-w, -w), (w, -w), (w, w), (-w, w)]:
        pts.append((x, y + origin_y, pylon3d.WAIST_Z * scale + origin_z))
    upper = []
    for (z, reach) in zip(pylon3d.ARM_LEVELS, pylon3d.ARM_REACH):
        upper.append((-reach * scale, origin_y, z * scale + origin_z))
        upper.append((reach * scale, origin_y, z * scale + origin_z))
    upper.append((0.0, origin_y,
                  (pylon3d.HEIGHT + pylon3d.PEAK_H) * scale + origin_z))
    return pts, upper


def visible(p, pos, fwd, right, up, tan_half):
    v = (p[0] - pos[0], p[1] - pos[1], p[2] - pos[2])
    depth = sum(a * b for a, b in zip(v, fwd))
    if depth <= 0.5:
        return False
    x = sum(a * b for a, b in zip(v, right))
    y = sum(a * b for a, b in zip(v, up))
    return abs(x) <= tan_half * depth and abs(y) <= tan_half * depth


def camera_basis(pos, aim):
    f = (aim[0] - pos[0], aim[1] - pos[1], aim[2] - pos[2])
    n = math.sqrt(sum(c * c for c in f))
    if n < 1e-6:
        raise ValueError("camera aim coincides with its position")
    fwd = tuple(c / n for c in f)
    world_up = (0.0, 1.0, 0.0) if abs(fwd[2]) > 0.999 else (0.0, 0.0, 1.0)
    r = (fwd[1] * world_up[2] - fwd[2] * world_up[1],
         fwd[2] * world_up[0] - fwd[0] * world_up[2],
         fwd[0] * world_up[1] - fwd[1] * world_up[0])
    rn = math.sqrt(sum(c * c for c in r))
    right = tuple(c / rn for c in r)
    up = (right[1] * fwd[2] - right[2] * fwd[1],
          right[2] * fwd[0] - right[0] * fwd[2],
          right[0] * fwd[1] - right[1] * fwd[0])
    return fwd, right, up


def _rng(issues, cfg, spec_val, key, label):
    lo, hi = cfg[key]
    if not isinstance(spec_val, (int, float)) or not lo <= spec_val <= hi:
        issues.append(f"{label} = {spec_val} out of range {lo}-{hi}")
        return False
    return True


def check(spec, cfg):
    issues = []

    terrain = spec.get("terrain")
    kind = (terrain or {}).get("kind")
    # The mountains kind is the vista: the camera may climb a slope and the fog runs
    # deeper, so its envelope overrides the base one.
    eff = dict(cfg)
    if kind in ("mountains", "farmland"):
        eff["cam_z"] = cfg["mountain_cam_z"]
        eff["fog_clear"] = cfg["mountain_fog_clear"]
        eff["fog_depth"] = cfg["mountain_fog_depth"]

    towers_ok = _rng(issues, cfg, spec.get("towers"), "towers", "towers")
    span_val = spec.get("span")
    if isinstance(span_val, list):
        # a per-gap list — the rhythm of the line
        span_ok = all(_rng(issues, cfg, g, "span", f"span[{i}]")
                      for i, g in enumerate(span_val))
        if towers_ok and len(span_val) != int(spec["towers"]) - 1:
            issues.append(f"span list has {len(span_val)} gaps for "
                          f"{spec['towers']} towers — need exactly towers - 1")
            span_ok = False
    else:
        span_ok = _rng(issues, cfg, span_val, "span", "span")
    _rng(issues, eff, spec.get("fog_clear"), "fog_clear", "fog_clear")
    _rng(issues, eff, spec.get("fog_depth"), "fog_depth", "fog_depth")
    scale = spec.get("tower_scale", 1.0)
    if not _rng(issues, cfg, scale, "tower_scale", "tower_scale"):
        scale = 1.0
    if spec.get("insulators", "I") not in ("I", "V"):
        issues.append(f"insulators = {spec.get('insulators')!r} — 'I' (single string) "
                      "or 'V' (v-string pair)")

    cam = spec.get("camera") or {}
    pos, aim = cam.get("pos"), cam.get("aim")
    cam_ok = True
    for name, v in (("camera.pos", pos), ("camera.aim", aim)):
        if not (isinstance(v, list) and len(v) == 3
                and all(isinstance(c, (int, float)) for c in v)):
            issues.append(f"{name} must be [x, y, z]")
            cam_ok = False
    lens_ok = _rng(issues, cfg, cam.get("lens"), "lens", "camera.lens")
    if cam_ok:
        lo, hi = eff["cam_z"]
        if not lo <= pos[2] <= hi:
            issues.append(f"camera.pos z = {pos[2]} out of range {lo}-{hi} — the camera "
                          "stays low; this series looks up")
        if aim[2] < cfg["aim_z_min"]:
            issues.append(f"camera.aim z = {aim[2]} < {cfg['aim_z_min']} — aim upward")

    sky = spec.get("sky") or {}
    horizon, zenith = sky.get("horizon"), sky.get("zenith")
    colors_ok = True
    for name, v in (("sky.horizon", horizon), ("sky.zenith", zenith),
                    ("steel", spec.get("steel"))):
        bad = oklch_range_issues(name, v)
        if bad:
            issues.extend(bad)
            colors_ok = False
    clouds = sky.get("clouds") or {}
    _rng(issues, cfg, clouds.get("scale", 2.4), "cloud_scale", "sky.clouds.scale")
    _rng(issues, cfg, clouds.get("strength", 0.45), "cloud_strength", "sky.clouds.strength")

    if colors_ok:
        # farmland allows the dawn gradient (warm horizon, cool zenith) the other
        # kinds forbid — still muted, never a full sunset
        max_de = (cfg["max_farm_horizon_zenith_de"] if kind == "farmland"
                  else cfg["max_horizon_zenith_de"])
        d = delta_e(horizon, zenith)
        if d > max_de:
            issues.append(
                f"horizon and zenith sit ΔE {d:.2f} apart (> {max_de}) "
                "— this is fog, not a sunset; keep the gradient gentle")
        d = delta_e(spec["steel"], horizon)
        if d < cfg["min_steel_sky_de"]:
            issues.append(
                f"steel sits only ΔE {d:.2f} from the horizon (< {cfg['min_steel_sky_de']}) "
                "— the subject must stand against the haze")

    terrain_ok = True
    if terrain:
        if kind not in ("snowfield", "mountains", "farmland"):
            issues.append(f"terrain.kind = {kind!r} — must be 'snowfield', 'mountains', "
                          "or 'farmland'")
            terrain_ok = False
        elif kind == "farmland":
            hedge = terrain.get("hedge") or {}
            hills = terrain.get("hills") or {}
            mist = terrain.get("mist") or {}
            _rng(issues, cfg, hedge.get("height", 6.0),
                 "farm_hedge_height", "terrain.hedge.height")
            terrain_ok &= _rng(issues, cfg, hills.get("relief", 70.0),
                               "farm_hills_relief", "terrain.hills.relief")
            terrain_ok &= _rng(issues, cfg, hills.get("scale", 800.0),
                               "farm_hills_scale", "terrain.hills.scale")
            _rng(issues, cfg, mist.get("height", 22.0),
                 "farm_mist_height", "terrain.mist.height")
            _rng(issues, cfg, mist.get("amount", 0.8),
                 "farm_mist_amount", "terrain.mist.amount")
            _rng(issues, cfg, terrain.get("facet", 22.0),
                 "farm_facet", "terrain.facet")
            _rng(issues, cfg, terrain.get("phase", 0.0), "phase", "terrain.phase")
            fcol = terrain.get("field")
            if fcol is not None:
                bad = oklch_range_issues("terrain.field", fcol)
                issues.extend(bad)
                if not bad and colors_ok:
                    d = delta_e(fcol, horizon)
                    if d < cfg["min_farm_sky_de"]:
                        issues.append(
                            f"terrain.field sits only ΔE {d:.2f} from the horizon "
                            f"(< {cfg['min_farm_sky_de']}) — the crop field underfoot "
                            "renders crisp and must read against the sky")
        else:
            prefix = "snow" if kind == "snowfield" else "mountain"
            defaults = (pylon3d.SNOW_DEFAULTS if kind == "snowfield"
                        else pylon3d.MOUNTAIN_DEFAULTS)
            terrain_ok &= _rng(issues, cfg, terrain.get("relief", defaults["relief"]),
                               f"{prefix}_relief", "terrain.relief")
            terrain_ok &= _rng(issues, cfg, terrain.get("scale", defaults["scale"]),
                               f"{prefix}_scale", "terrain.scale")
            _rng(issues, cfg, terrain.get("phase", 0.0), "phase", "terrain.phase")
            _rng(issues, cfg, terrain.get("shade", sum(cfg[f"{prefix}_shade"]) / 2),
                 f"{prefix}_shade", "terrain.shade")
            _rng(issues, cfg, terrain.get("facet", 11.0 if kind == "snowfield" else 80.0),
                 f"{prefix}_facet", "terrain.facet")
            if kind == "mountains":
                terrain_ok &= _rng(issues, cfg, terrain.get("sharp", defaults["sharp"]),
                                   "mountain_sharp", "terrain.sharp")
        tcol = terrain.get("color")
        if tcol is not None:
            bad = oklch_range_issues("terrain.color", tcol)
            issues.extend(bad)
            if not bad and colors_ok:
                d = delta_e(tcol, horizon)
                if kind == "snowfield" and d > cfg["max_snow_sky_de"]:
                    issues.append(
                        f"terrain.color sits ΔE {d:.2f} from the horizon "
                        f"(> {cfg['max_snow_sky_de']}) — snow is nearly one with the "
                        "sky; only its drift shadows may read")
                if kind == "mountains" and d < cfg["min_mountain_sky_de"]:
                    issues.append(
                        f"terrain.color sits only ΔE {d:.2f} from the horizon "
                        f"(< {cfg['min_mountain_sky_de']}) — the near ground renders "
                        "crisp and must stand against the haze")

    if not (towers_ok and span_ok and cam_ok and lens_ok):
        return issues

    # Geometry: project the nearest tower through the camera. Tower base heights come
    # from the terrain function — the same one the renderer evaluates.
    count = int(spec["towers"])
    elevations = ((pylon3d.tower_elevations(terrain, count, span_val) or [0.0] * count)
                  if terrain_ok else [0.0] * count)
    ys = pylon3d.tower_ys(count, span_val)
    ni = min(range(len(ys)), key=lambda i: abs(ys[i] - pos[1]))
    nearest_y, nearest_z = ys[ni], elevations[ni]
    base_pts, upper_pts = keypoints(nearest_y, nearest_z, scale)
    try:
        fwd, right, up = camera_basis(pos, aim)
    except ValueError as e:
        return issues + [str(e)]
    tan_half = 18.0 / cam["lens"]

    n_vis = sum(visible(p, pos, fwd, right, up, tan_half) for p in base_pts + upper_pts)
    upper_vis = sum(visible(p, pos, fwd, right, up, tan_half) for p in upper_pts)
    if n_vis < cfg["min_visible_keypoints"]:
        issues.append(
            f"only {n_vis} keypoints of the nearest tower land in frame "
            f"(< {cfg['min_visible_keypoints']}) — the camera is not looking at the subject")
    elif cfg.get("require_upper_keypoint") and upper_vis == 0:
        issues.append(
            "no arm tip or peak of the nearest tower is in frame — only legs; raise the "
            "aim or step back")

    # In a landscape piece the nearest tower stands wholly inside the frame — the
    # looming crop belongs to the bare-sky close-ups only
    if kind:
        tight = tan_half * 0.94
        pts = base_pts + upper_pts
        n_full = sum(visible(p, pos, fwd, right, up, tight) for p in pts)
        if n_full < len(pts):
            issues.append(
                f"only {n_full}/{len(pts)} keypoints of the nearest tower fit inside "
                "the frame with margin — landscape pieces contain the whole tower; "
                "step back, widen the lens, or re-aim")

    # A landscape that never enters the frame is no landscape: the ray through the
    # bottom-center of the frame must reach the ground close enough that the field
    # renders crisp under the tower
    reach = {"snowfield": cfg.get("snow_ground_reach"),
             "farmland": cfg.get("farm_ground_reach")}.get(kind)
    if reach:
        bd = tuple(f - tan_half * u for f, u in zip(fwd, up))
        n = math.sqrt(sum(c * c for c in bd))
        dz = bd[2] / n
        if dz >= -1e-6 or pos[2] / -dz > reach:
            issues.append(
                f"the bottom of the frame does not reach the ground within {reach} m — "
                "lower the aim or step farther back so the field shows under the tower")

    # The hedgerow sits in front of the camera at a readable distance
    if kind == "farmland":
        hd = (terrain.get("hedge") or {}).get("distance", 240.0)
        if isinstance(hd, (int, float)):
            _rng(issues, cfg, hd - pos[1], "farm_hedge_distance",
                 "hedge distance in front of the camera (hedge y - camera y)")

    # The camera stands on the ground, not inside it
    if terrain and terrain_ok:
        ground = pylon3d.terrain_height_at(terrain, pos[0], pos[1], count, span_val)
        if pos[2] < ground + 0.3:
            issues.append(
                f"camera.pos z = {pos[2]} but the terrain there is {ground:.1f} m high — "
                "the camera is inside the ground; rise above it or move")

    top = (pylon3d.HEIGHT + pylon3d.PEAK_H) * scale
    peak = (0.0, nearest_y, top + nearest_z)
    d_peak = math.dist(pos, peak)
    fog_clear, fog_depth = spec.get("fog_clear"), spec.get("fog_depth")
    fog_num = all(isinstance(v, (int, float)) for v in (fog_clear, fog_depth))
    if kind in ("mountains", "farmland"):
        # Anchor-and-layers composition: the first tower is the crisp foreground
        # anchor, the receding line stays countable through the haze.
        if count < cfg["mountain_min_towers"]:
            issues.append(
                f"{count} towers (< {cfg['mountain_min_towers']}) — this "
                "composition lives on the rhythm of a receding line")
        lo, hi = cfg["mountain_tower_dist"]
        if not lo <= d_peak <= hi:
            issues.append(
                f"the nearest tower is {d_peak:.0f} m from the camera, outside {lo}-{hi} — "
                "it is the foreground anchor: near, large, and crisp")
        else:
            if fog_num and d_peak > fog_clear:
                issues.append(
                    f"the anchor tower's peak is {d_peak:.0f} m away but fog_clear is "
                    f"{fog_clear} — the anchor renders crisp; raise fog_clear")
            frac = (top * cam["lens"]) / (36.0 * d_peak)
            if frac < cfg["min_anchor_frac"]:
                issues.append(
                    f"the anchor tower stands only {frac:.2f} of the frame height "
                    f"(< {cfg['min_anchor_frac']}) — move closer or use a longer lens")
            elif frac > cfg["max_anchor_frac"]:
                issues.append(
                    f"the anchor tower stands {frac:.2f} of the frame height "
                    f"(> {cfg['max_anchor_frac']}) — it needs air around it; step "
                    "back or widen the lens")
        d_far = max(math.dist(pos, (0.0, y, top + e))
                    for y, e in zip(ys, elevations))
        if fog_num:
            mix = 1.0 - math.exp(-max(0.0, d_far - fog_clear) / max(1.0, fog_depth))
            if mix > cfg["max_far_dissolve"]:
                issues.append(
                    f"the farthest tower is {mix:.2f} dissolved into the haze "
                    f"(> {cfg['max_far_dissolve']}) — every tower stays countable; "
                    "deepen fog_depth, raise fog_clear, or shorten the line")
    else:
        if fog_num and d_peak > fog_clear:
            # The sharp-subject rule: the nearest tower fits inside the fog's clear zone
            issues.append(
                f"the nearest tower's peak is {d_peak:.0f} m from the camera but "
                f"fog_clear is {fog_clear} — the subject must be entirely sharp; raise "
                "fog_clear or move closer")
        if kind == "snowfield":
            frac = (top * cam["lens"]) / (36.0 * d_peak)
            _rng(issues, cfg, frac, "snow_anchor_frac",
                 "the tower's share of the frame height (height x lens / (36 x dist))")

    return issues
