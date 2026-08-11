"""Render a run of lattice towers in true 3D — beveled curves for every member, so wires
and steel are continuous lines, an emission-only world (fog, mist, and slope shading are
all material tricks, no lights), and optionally a low-poly faceted landscape.

Usage:
  blender --background --factory-startup --python scripts/render_pylon3d.py -- \
    <spec.json> <out.png> [--draft]

The spec schema is documented in rulesets/pylon-series/rules.md — the same JSON the
sampler asks the model to emit is what this script renders.
"""

import json
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pylon3d  # noqa: E402
from colorutil import oklch_to_linear_srgb  # noqa: E402
from validate_pylon import camera_basis, visible  # noqa: E402

# Member radii (meters). Conductors are exaggerated slightly past reality so they still
# read as lines at distance; everything else is close to actual angle-steel scale.
RADII = {"legs": 0.15, "braces": 0.065, "arms": 0.095, "insulators": 0.10, "wires": 0.045}

# The fog theme, in the project's OKLCH. Horizon is the densest haze (near white); the
# zenith keeps slightly more color; clouds are brighter patches barely lifted from it.
DEFAULT_SKY = {
    "horizon": [0.945, 0.010, 235],
    "zenith": [0.860, 0.028, 238],
    "clouds": {"scale": 2.4, "detail": 6.0, "strength": 0.45},
}
DEFAULT_STEEL = [0.22, 0.015, 250]
# Fog belongs to the background. Nothing inside FOG_CLEAR meters is touched at all — the
# subject pylon stays graphic-sharp — and beyond it the haze builds over FOG_DEPTH.
DEFAULT_FOG_CLEAR = 75.0
DEFAULT_FOG_DEPTH = 130.0
DEFAULT_CAM = {"pos": [5.0, -10.0, 1.6], "aim": [0.0, 18.0, 34.0], "lens": 24}


def rgb(oklch):
    return (*oklch_to_linear_srgb(*oklch), 1.0)


def build_sky(scn, sky_spec):
    """A hazy sky: horizon-to-zenith gradient with a soft cloud mottle. Pure emission —
    the world lights nothing, it only appears."""
    horizon = rgb(sky_spec.get("horizon", DEFAULT_SKY["horizon"]))
    zenith = rgb(sky_spec.get("zenith", DEFAULT_SKY["zenith"]))
    clouds = {**DEFAULT_SKY["clouds"], **(sky_spec.get("clouds") or {})}
    # cloud patches sit a touch brighter than the local sky — fog-lit, not cumulus
    cloud_col = rgb(sky_spec.get("cloud_color")
                    or [min(1.0, sky_spec.get("horizon", DEFAULT_SKY["horizon"])[0] + 0.035),
                        sky_spec.get("horizon", DEFAULT_SKY["horizon"])[1] * 0.6,
                        sky_spec.get("horizon", DEFAULT_SKY["horizon"])[2]])

    world = bpy.data.worlds.new("world")
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = 1.0

    coord = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(coord.outputs["Generated"], sep.inputs["Vector"])
    ramp = nt.nodes.new("ShaderNodeMapRange")           # view Z -> gradient factor
    ramp.inputs["From Min"].default_value = 0.0
    ramp.inputs["From Max"].default_value = 0.85
    ramp.clamp = True
    nt.links.new(sep.outputs["Z"], ramp.inputs["Value"])

    grad = nt.nodes.new("ShaderNodeMix")
    grad.data_type = "RGBA"
    grad.inputs["A"].default_value = horizon
    grad.inputs["B"].default_value = zenith
    nt.links.new(ramp.outputs["Result"], grad.inputs["Factor"])

    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = clouds["scale"]
    noise.inputs["Detail"].default_value = clouds["detail"]
    noise.inputs["Roughness"].default_value = 0.55
    nt.links.new(coord.outputs["Generated"], noise.inputs["Vector"])
    softness = nt.nodes.new("ShaderNodeMapRange")       # low-contrast cloud factor
    softness.inputs["From Min"].default_value = 0.35
    softness.inputs["From Max"].default_value = 0.75
    softness.inputs["To Max"].default_value = clouds["strength"]
    softness.clamp = True
    nt.links.new(noise.outputs["Fac"], softness.inputs["Value"])

    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs["B"].default_value = cloud_col
    nt.links.new(grad.outputs["Result"], mix.inputs["A"])
    nt.links.new(softness.outputs["Result"], mix.inputs["Factor"])

    nt.links.new(mix.outputs["Result"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    scn.world = world
    return horizon


def _fog_factor(nt, fog_clear, fog_depth):
    """Node chain for the fog mix factor: 1 - exp(-max(0, d - clear) / depth). Returns
    the output socket."""
    cam = nt.nodes.new("ShaderNodeCameraData")
    sub = nt.nodes.new("ShaderNodeMath")
    sub.operation = "SUBTRACT"
    sub.inputs[1].default_value = max(0.0, fog_clear)
    nt.links.new(cam.outputs["View Distance"], sub.inputs[0])
    clamp = nt.nodes.new("ShaderNodeMath")
    clamp.operation = "MAXIMUM"
    clamp.inputs[1].default_value = 0.0
    nt.links.new(sub.outputs["Value"], clamp.inputs[0])
    div = nt.nodes.new("ShaderNodeMath")
    div.operation = "DIVIDE"
    div.inputs[1].default_value = max(1.0, fog_depth)
    nt.links.new(clamp.outputs["Value"], div.inputs[0])
    neg = nt.nodes.new("ShaderNodeMath")
    neg.operation = "MULTIPLY"
    neg.inputs[1].default_value = -1.0
    nt.links.new(div.outputs["Value"], neg.inputs[0])
    exp = nt.nodes.new("ShaderNodeMath")
    exp.operation = "EXPONENT"
    nt.links.new(neg.outputs["Value"], exp.inputs[0])
    fac = nt.nodes.new("ShaderNodeMath")
    fac.operation = "SUBTRACT"
    fac.inputs[0].default_value = 1.0
    nt.links.new(exp.outputs["Value"], fac.inputs[1])
    return fac.outputs["Value"]


def _mist_factor(nt, mist_height, mist_amount, gate):
    """Ground mist: strongest at z = 0, gone by z = mist_height — and only past the
    anchor zone (`gate`), so the near subject stays out of it. Hills and far towers
    rise out of the white; their feet whiten first."""
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(geo.outputs["Position"], sep.inputs["Vector"])
    hm = nt.nodes.new("ShaderNodeMapRange")
    hm.inputs["From Min"].default_value = 0.0
    hm.inputs["From Max"].default_value = mist_height
    hm.inputs["To Min"].default_value = mist_amount
    hm.inputs["To Max"].default_value = 0.0
    hm.clamp = True
    nt.links.new(sep.outputs["Z"], hm.inputs["Value"])
    cam = nt.nodes.new("ShaderNodeCameraData")
    g = nt.nodes.new("ShaderNodeMapRange")
    g.inputs["From Min"].default_value = gate
    g.inputs["From Max"].default_value = gate * 2.2
    g.clamp = True
    nt.links.new(cam.outputs["View Distance"], g.inputs["Value"])
    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    nt.links.new(hm.outputs["Result"], mul.inputs[0])
    nt.links.new(g.outputs["Result"], mul.inputs[1])
    return mul.outputs["Value"]


def _mist_mix(nt, color_socket, mist):
    """Mix a color socket toward the mist color by the mist factor."""
    height, amount, gate, mist_rgba = mist
    mm = nt.nodes.new("ShaderNodeMix")
    mm.data_type = "RGBA"
    mm.inputs["B"].default_value = mist_rgba
    nt.links.new(color_socket, mm.inputs["A"])
    nt.links.new(_mist_factor(nt, height, amount, gate), mm.inputs["Factor"])
    return mm.outputs["Result"]


def fog_material(name, steel_oklch, horizon_rgba, fog_depth, fog_clear, mist=None):
    """Emission whose color dissolves into the horizon haze with camera distance — but
    only past the clear zone.

    The clear zone keeps the subject entirely out of the fog: the near pylon renders
    uniformly sharp, and only what stands behind it belongs to the haze. No lights
    involved; a wire crossing the boundary fades smoothly along its own length.
    With `mist`, ground mist whitens whatever stands low and far — the far towers'
    legs sink into it before their peaks do."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 1.0

    base = nt.nodes.new("ShaderNodeRGB")
    base.outputs["Color"].default_value = rgb(steel_oklch)
    color = base.outputs["Color"]
    if mist:
        color = _mist_mix(nt, color, mist)
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs["B"].default_value = horizon_rgba
    nt.links.new(color, mix.inputs["A"])
    nt.links.new(_fog_factor(nt, fog_clear, fog_depth), mix.inputs["Factor"])
    nt.links.new(mix.outputs["Result"], em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return mat


def terrain_material(name, oklch, horizon_rgba, fog_depth, fog_clear, shade,
                     mist=None, jitter=0.1):
    """Terrain emission = base color × slope shading × per-facet tone jitter, then
    mist, then distance fog.

    There are no lights in this world, so relief has to shade itself: the surface normal
    is dotted with a fixed high 'light' direction and slopes facing away darken by up to
    `shade`. On the flat-shaded low-poly meshes every facet takes a single tone from
    this. `jitter` adds the low-poly patchwork: the facet normal (constant per face)
    hashed through white noise gives each facet its own fixed tone offset."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 1.0

    geo = nt.nodes.new("ShaderNodeNewGeometry")
    dot = nt.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    # a lower 'light' and a tight window: the gentle tilt differences between facets
    # must spread into distinct tone steps — the whole point of the faceted land
    dot.inputs[1].default_value = (0.45, -0.25, 0.86)
    nt.links.new(geo.outputs["Normal"], dot.inputs[0])
    ramp = nt.nodes.new("ShaderNodeMapRange")
    ramp.inputs["From Min"].default_value = 0.6
    ramp.inputs["From Max"].default_value = 1.0
    ramp.inputs["To Min"].default_value = 1.0 - shade
    ramp.inputs["To Max"].default_value = 1.0
    ramp.clamp = True
    nt.links.new(dot.outputs["Value"], ramp.inputs["Value"])

    shaded = nt.nodes.new("ShaderNodeMix")
    shaded.data_type = "RGBA"
    shaded.blend_type = "MULTIPLY"
    shaded.inputs["Factor"].default_value = 1.0
    shaded.inputs["A"].default_value = rgb(oklch)
    nt.links.new(ramp.outputs["Result"], shaded.inputs["B"])
    color = shaded.outputs["Result"]

    if jitter > 0.0:
        wn = nt.nodes.new("ShaderNodeTexWhiteNoise")
        wn.noise_dimensions = "3D"
        nt.links.new(geo.outputs["Normal"], wn.inputs["Vector"])
        jr = nt.nodes.new("ShaderNodeMapRange")
        jr.inputs["From Min"].default_value = 0.0
        jr.inputs["From Max"].default_value = 1.0
        jr.inputs["To Min"].default_value = 1.0 - jitter
        jr.inputs["To Max"].default_value = 1.0 + jitter * 0.4
        nt.links.new(wn.outputs["Value"], jr.inputs["Value"])
        jm = nt.nodes.new("ShaderNodeMix")
        jm.data_type = "RGBA"
        jm.blend_type = "MULTIPLY"
        jm.inputs["Factor"].default_value = 1.0
        nt.links.new(color, jm.inputs["A"])
        nt.links.new(jr.outputs["Result"], jm.inputs["B"])
        color = jm.outputs["Result"]
    if mist:
        color = _mist_mix(nt, color, mist)
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs["B"].default_value = horizon_rgba
    nt.links.new(color, mix.inputs["A"])
    nt.links.new(_fog_factor(nt, fog_clear, fog_depth), mix.inputs["Factor"])
    nt.links.new(mix.outputs["Result"], em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return mat


def curve_object(name, bevel, mat, scn):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = bevel
    cu.bevel_resolution = 3
    cu.fill_mode = "FULL"
    obj = bpy.data.objects.new(name, cu)
    scn.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return cu


def add_segments(cu, segments):
    for p0, p1 in segments:
        sp = cu.splines.new("POLY")
        sp.points.add(1)
        sp.points[0].co = (*p0, 1.0)
        sp.points[1].co = (*p1, 1.0)


def add_polylines(cu, lines):
    for pts in lines:
        sp = cu.splines.new("POLY")
        sp.points.add(len(pts) - 1)
        for i, p in enumerate(pts):
            sp.points[i].co = (*p, 1.0)


DEFAULT_MOUNTAIN_COLOR = [0.30, 0.030, 140]
DEFAULT_FIELD_COLOR = [0.62, 0.045, 100]
DEFAULT_HEDGE_COLOR = [0.28, 0.045, 145]


def farm_mist(terrain, sky_horizon_oklch, fog_clear, cam_y):
    """Mist spec tuple for the farmland kind: (height, amount, gate, rgba). The mist
    color is the horizon lifted and desaturated — paler than the haze it sits in.
    It pools BEHIND the hedgerow (as in the reference photograph), so the gate is
    the camera's distance to the hedge: the field and the hedge stay out of it."""
    mist = terrain.get("mist") or {}
    hedge_d = (terrain.get("hedge") or {}).get("distance", 240.0)
    mist_rgba = rgb([min(1.0, sky_horizon_oklch[0] + 0.04),
                     sky_horizon_oklch[1] * 0.2, sky_horizon_oklch[2]])
    return (mist.get("height", 22.0), mist.get("amount", 0.8),
            max(fog_clear, hedge_d - cam_y + 20.0), mist_rgba)


def mesh_object(name, verts, faces, mat, scn):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    # flat shading, explicitly — each facet must keep its own single tone
    me.polygons.foreach_set("use_smooth", [False] * len(me.polygons))
    me.update()
    obj = bpy.data.objects.new(name, me)
    scn.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj


def build_terrain(scn, terrain, sky_horizon_oklch, horizon, fog_depth, fog_clear,
                  towers, span, cam_y):
    """One displaced landscape mesh per kind — real geometry with slope shading, seated
    into depth by the same distance fog that fades the far towers."""
    kind = terrain.get("kind")
    if kind == "snowfield":
        # snow is near the camera and keeps the clear zone — its whole reading is
        # faint facet shading, which distance fog would erase. Facets stay small so
        # the settled wells around the tower legs resolve.
        color = terrain.get("color") or [max(0.0, sky_horizon_oklch[0] - 0.035),
                                         sky_horizon_oklch[1] * 0.9,
                                         sky_horizon_oklch[2]]
        mat = terrain_material("snow", color, horizon, fog_depth, fog_clear,
                               terrain.get("shade", 0.15), jitter=0.05)
        verts, faces = pylon3d.tri_mesh(
            -840, 840, -350, 2100, terrain.get("facet", 11.0),
            lambda x, y: pylon3d.terrain_height_at(terrain, x, y, towers, span))
        mesh_object("snow", verts, faces, mat, scn)
    elif kind == "mountains":
        # mountain country fades from the camera outward with no clear zone — a clear
        # zone on the flat valley floor shows up as a hard onset line across the frame
        mat = terrain_material("mountains", terrain.get("color", DEFAULT_MOUNTAIN_COLOR),
                               horizon, fog_depth, 0.0,
                               terrain.get("shade", 0.35), jitter=0.12)
        verts, faces = pylon3d.tri_mesh(
            -2600, 2600, -450, 4200, terrain.get("facet", 80.0),
            lambda x, y: pylon3d.terrain_height_at(terrain, x, y))
        mesh_object("mountains", verts, faces, mat, scn)
    elif kind == "farmland":
        # rolling faceted field, a clumped hedgerow band, ground mist pooled behind
        # it, low hills rising out of the mist
        field = terrain.get("field", DEFAULT_FIELD_COLOR)
        m = farm_mist(terrain, sky_horizon_oklch, fog_clear, cam_y)
        mat = terrain_material("field", field, horizon, fog_depth, fog_clear,
                               terrain.get("shade", 0.4), mist=m, jitter=0.13)
        verts, faces = pylon3d.tri_mesh(
            -840, 840, -350, 3200, terrain.get("facet", 22.0),
            lambda x, y: pylon3d.terrain_height_at(terrain, x, y))
        mesh_object("field", verts, faces, mat, scn)
        ph, _hr, _hs, hd = pylon3d.farm_args(terrain)
        hmat = terrain_material("hedge", terrain.get("hedge_color", DEFAULT_HEDGE_COLOR),
                                horizon, fog_depth, fog_clear, 0.3)
        verts, faces = pylon3d.hedge_mesh(
            hd, (terrain.get("hedge") or {}).get("height", 6.0), ph)
        mesh_object("hedge", verts, faces, hmat, scn)


def lead_ends_off_frame(spec, elevations, lead_gap):
    """True when the lead wires' cut ends would land outside the camera frustum (with
    margin) — then a vista can carry wires past its anchor tower without loose ends
    hanging in view. Pure function of the spec: reproducibility holds."""
    cam = spec.get("camera") or {}
    pos = cam.get("pos", DEFAULT_CAM["pos"])
    aim = cam.get("aim", DEFAULT_CAM["aim"])
    tan_half = 18.0 / cam.get("lens", DEFAULT_CAM["lens"]) * 1.15
    fwd, right, up = camera_basis(pos, aim)
    t0 = pylon3d.tower(origin_y=0.0, origin_z=(elevations or [0.0])[0],
                       scale=spec.get("tower_scale", 1.0),
                       v_strings=spec.get("insulators", "I") == "V")
    ends = [(a[0], a[1] - lead_gap, a[2]) for a in t0["attach"].values()]
    return not any(visible(p, pos, fwd, right, up, tan_half) for p in ends)


def build(spec):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scn = bpy.context.scene

    horizon = build_sky(scn, spec.get("sky") or {})
    fog_depth = spec.get("fog_depth", DEFAULT_FOG_DEPTH)
    fog_clear = spec.get("fog_clear", DEFAULT_FOG_CLEAR)
    sky_oklch = (spec.get("sky") or {}).get("horizon", DEFAULT_SKY["horizon"])
    # in farmland the far towers' legs sink into the ground mist along with the hills
    cam_y = (spec.get("camera", {}).get("pos") or DEFAULT_CAM["pos"])[1]
    steel_mist = (farm_mist(spec["terrain"], sky_oklch, fog_clear, cam_y)
                  if (spec.get("terrain") or {}).get("kind") == "farmland" else None)
    mat = fog_material("steel", spec.get("steel", DEFAULT_STEEL), horizon,
                       fog_depth, fog_clear, mist=steel_mist)

    towers, span = spec.get("towers", 3), spec.get("span", 120.0)
    kind = (spec.get("terrain") or {}).get("kind")
    lead_gap = span[0] if isinstance(span, (list, tuple)) else span
    elevations = pylon3d.tower_elevations(spec.get("terrain"), int(towers), span)
    # Lead half-span wires continue over the camera region. A vista used to drop them
    # outright (cut ends in the air); now it carries them whenever every cut end stays
    # safely off-frame — a line that dead-ends at its anchor tower reads wrong.
    lead = kind not in ("mountains", "farmland") or cam_y >= -lead_gap
    if not lead:
        lead = lead_ends_off_frame(spec, elevations, lead_gap)
    geo = pylon3d.line_of_towers(
        towers, span, elevations, lead=lead,
        scale=spec.get("tower_scale", 1.0),
        v_strings=spec.get("insulators", "I") == "V")
    for group in ("legs", "braces", "arms", "insulators"):
        cu = curve_object(group, RADII[group], mat, scn)
        add_segments(cu, geo[group])
    cu = curve_object("wires", RADII["wires"], mat, scn)
    add_polylines(cu, geo["wires"])

    if spec.get("terrain"):
        build_terrain(scn, spec["terrain"], sky_oklch,
                      horizon, fog_depth, fog_clear, int(towers), span, cam_y)

    cam_spec = spec.get("camera", {})
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = cam_spec.get("lens", DEFAULT_CAM["lens"])
    cam = bpy.data.objects.new("cam", cam_data)
    scn.collection.objects.link(cam)
    pos = Vector(cam_spec.get("pos", DEFAULT_CAM["pos"]))
    aim = Vector(cam_spec.get("aim", DEFAULT_CAM["aim"]))
    cam.location = pos
    cam.rotation_euler = (aim - pos).to_track_quat("-Z", "Y").to_euler()
    scn.camera = cam

    r = spec.get("render", {})
    scn.render.engine = "CYCLES"
    scn.cycles.samples = r.get("samples", 16)
    scn.cycles.use_denoising = False
    res = r.get("resolution", 2048)
    scn.render.resolution_x = res
    scn.render.resolution_y = res
    scn.render.image_settings.file_format = "PNG"
    scn.view_settings.view_transform = "Standard"
    scn.view_settings.look = "None"
    scn.render.dither_intensity = 0.0
    return scn


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    spec_path, out_path = argv[0], argv[1]
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)
    if "--draft" in argv:
        spec.setdefault("render", {})["resolution"] = 640
    scn = build(spec)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    scn.render.filepath = os.path.abspath(out_path)
    bpy.ops.render.render(write_still=True)
    print(f"[pylon3d] {spec_path} -> {out_path}")


if __name__ == "__main__":
    main()
