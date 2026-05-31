"""generator.py - prompt -> LIVE Blender -> baked-animation USDZ.

Drives the user's open Blender (via blender_bridge / socket localhost:9876) to
model + animate a text prompt, then exports an AR-ready USDZ that matches the
studio export settings in studio/blender/build_studio.py::_export_usdz
(Y-up, metres, baked looping animation, generate_preview_surface), per
docs/generative_ar_contract.md.

No headless Blender is spawned - the live Blender does the work and stays open.

This is a procedural v0.1 generator: it recognises a shape, colour, motion and
size from the prompt and composes them. It is deliberately a single clean module
so the "prompt -> spec" front end can later be swapped for an LLM-backed one
without touching the server or the export contract.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_bridge as bridge  # noqa: E402

FPS = 30
FRAMES = 120                     # 4.0 s seamless loop (matches studio CYCLE)
SECONDS = round(FRAMES / FPS, 2)
MAX_DIM_TARGET = 0.9             # <= 1 m tabletop footprint, incl. motion, w/ margin

# --- prompt vocabulary --------------------------------------------------------
# colour -> (rgb, roughness, metallic)
COLORS = {
    "red": ((0.85, 0.10, 0.10), 0.45, 0.0),
    "crimson": ((0.72, 0.06, 0.12), 0.45, 0.0),
    "scarlet": ((0.91, 0.16, 0.10), 0.45, 0.0),
    "orange": ((0.95, 0.45, 0.08), 0.5, 0.0),
    "yellow": ((0.96, 0.80, 0.12), 0.5, 0.0),
    "gold": ((0.90, 0.72, 0.20), 0.30, 0.9),
    "green": ((0.13, 0.65, 0.22), 0.5, 0.0),
    "lime": ((0.55, 0.85, 0.18), 0.5, 0.0),
    "teal": ((0.10, 0.62, 0.60), 0.45, 0.0),
    "cyan": ((0.10, 0.72, 0.82), 0.40, 0.0),
    "blue": ((0.12, 0.35, 0.85), 0.45, 0.0),
    "navy": ((0.08, 0.16, 0.45), 0.45, 0.0),
    "purple": ((0.45, 0.20, 0.72), 0.5, 0.0),
    "violet": ((0.55, 0.30, 0.85), 0.5, 0.0),
    "pink": ((0.95, 0.45, 0.65), 0.5, 0.0),
    "magenta": ((0.85, 0.10, 0.55), 0.5, 0.0),
    "white": ((0.92, 0.92, 0.92), 0.5, 0.0),
    "black": ((0.05, 0.05, 0.06), 0.5, 0.0),
    "grey": ((0.50, 0.50, 0.52), 0.5, 0.0),
    "gray": ((0.50, 0.50, 0.52), 0.5, 0.0),
    "silver": ((0.80, 0.80, 0.82), 0.25, 0.9),
    "brown": ((0.40, 0.26, 0.13), 0.7, 0.0),
}
DEFAULT_COLOR = ((0.50, 0.55, 0.62), 0.45, 0.0)

# keyword -> canonical shape
SHAPES = {
    "sphere": "sphere", "ball": "sphere", "orb": "sphere", "globe": "sphere",
    "marble": "sphere", "planet": "sphere", "moon": "sphere", "bubble": "sphere",
    "cube": "cube", "box": "cube", "block": "cube", "crate": "cube",
    "dice": "cube", "die": "cube", "square": "cube",
    "cylinder": "cylinder", "can": "cylinder", "tube": "cylinder",
    "pipe": "cylinder", "barrel": "cylinder", "drum": "cylinder", "pillar": "cylinder",
    "cone": "cone", "funnel": "cone", "icecream": "cone",
    "pyramid": "pyramid",
    "torus": "torus", "donut": "torus", "doughnut": "torus", "ring": "torus",
    "tire": "torus", "tyre": "torus", "bagel": "torus",
}
DEFAULT_SHAPE = "sphere"

# keyword -> canonical motion (priority order matters: first hit wins)
MOTION_KEYWORDS = [
    ("bounce", ("bounce", "bouncing", "bouncy")),
    ("bounce", ("drop", "dropping", "fall", "falling")),  # drop reads as a bounce
    ("roll", ("roll", "rolling")),
    ("orbit", ("orbit", "orbiting", "revolve", "revolving")),
    ("hover", ("hover", "hovering", "float", "floating", "levitate", "levitating")),
    ("pulse", ("pulse", "pulsing", "throb", "throbbing", "beat", "beating", "breathe")),
    ("wobble", ("wobble", "wobbling", "jiggle", "jiggling", "shake", "shaking")),
    ("spin", ("spin", "spinning", "rotate", "rotating", "twirl", "whirl", "turn", "turning")),
]
DEFAULT_MOTION = "spin"

# size word -> nominal max object dimension (metres)
SIZES = {
    "tiny": 0.12, "miniature": 0.12,
    "small": 0.20, "little": 0.20,
    "big": 0.45, "large": 0.45,
    "huge": 0.60, "giant": 0.60, "massive": 0.60, "enormous": 0.60,
}
DEFAULT_SIZE = 0.30

# distance from an object's origin to its lowest point, as a fraction of `size`
_FOOT_FRAC = {
    "sphere": 0.5, "cube": 0.5, "cylinder": 0.5,
    "cone": 0.5, "pyramid": 0.5, "torus": 0.15,  # torus minor_radius = size*0.15
}


def parse_prompt(prompt: str) -> dict:
    """Turn a free-text prompt into a deterministic build spec."""
    p = (prompt or "").lower()
    words = set(re.findall(r"[a-z]+", p))

    color_rgb, rough, metal = DEFAULT_COLOR
    color_name = "default"
    for name, (rgb, rgh, mtl) in COLORS.items():
        if name in words:
            color_rgb, rough, metal, color_name = rgb, rgh, mtl, name
            break

    shape = DEFAULT_SHAPE
    for kw, canon in SHAPES.items():
        if kw in words:
            shape = canon
            break

    motion = DEFAULT_MOTION
    for canon, kws in MOTION_KEYWORDS:
        if any(k in words for k in kws):
            motion = canon
            break

    size = DEFAULT_SIZE
    for kw, val in SIZES.items():
        if kw in words:
            size = val
            break

    foot = size * _FOOT_FRAC.get(shape, 0.5)
    # bounce apex (height of the object's *bottom* above the floor); shrink for big objects
    apex = max(0.16, min(0.40, 0.70 - 2.0 * foot))

    return {
        "prompt": prompt,
        "shape": shape,
        "color": list(color_rgb),
        "color_name": color_name,
        "rough": rough,
        "metal": metal,
        "motion": motion,
        "size": round(size, 4),
        "foot": round(foot, 4),
        "apex": round(apex, 4),
        "bounces": 2,
        "fps": FPS,
        "frames": FRAMES,
        "max_dim_target": MAX_DIM_TARGET,
        "name": "gen_object",
        "root_name": "gen_root",
    }


# --- Blender code templates (executed over the socket; must self-import) -------
_PRELUDE = (
    "import bpy, math, json\n"
    "from mathutils import Vector\n"
    "P = json.loads(%r)\n"
)

_BUILD_ANIMATE = r"""
name = P["name"]; root_name = P["root_name"]
# --- clean slate WITHOUT reloading prefs (read_factory_settings is sandbox-blocked
#     and would kill the live MCP server); remove objects + orphan data instead ---
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)
for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.curves,
             bpy.data.lights, bpy.data.cameras):
    for blk in list(coll):
        if blk.users == 0:
            coll.remove(blk)

sc = bpy.context.scene
sc.render.fps = int(P["fps"]); sc.frame_start = 1; sc.frame_end = int(P["frames"])

shape = P["shape"]; S = float(P["size"]); foot = float(P["foot"])
if shape == "cube":
    bpy.ops.mesh.primitive_cube_add(size=S)
elif shape == "cylinder":
    bpy.ops.mesh.primitive_cylinder_add(radius=S/2.0, depth=S, vertices=56)
elif shape == "cone":
    bpy.ops.mesh.primitive_cone_add(radius1=S/2.0, depth=S, vertices=56)
elif shape == "pyramid":
    bpy.ops.mesh.primitive_cone_add(radius1=S/2.0*1.3, depth=S, vertices=4)
elif shape == "torus":
    bpy.ops.mesh.primitive_torus_add(major_radius=S/2.0*0.72, minor_radius=S*0.15)
else:
    bpy.ops.mesh.primitive_uv_sphere_add(radius=S/2.0, segments=56, ring_count=28)
o = bpy.context.active_object
o.name = name
try:
    bpy.ops.object.shade_smooth()
except Exception:
    pass

# --- PBR material (UsdPreviewSurface comes from the Principled BSDF) ---
col = P["color"]
mat = bpy.data.materials.new(name + "_mat")
mat.use_nodes = True
bsdf = mat.node_tree.nodes.get("Principled BSDF")
if bsdf:
    bsdf.inputs["Base Color"].default_value = (col[0], col[1], col[2], 1.0)
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = float(P["rough"])
    if "Metallic" in bsdf.inputs:
        bsdf.inputs["Metallic"].default_value = float(P["metal"])
mat.diffuse_color = (col[0], col[1], col[2], 1.0)
o.data.materials.append(mat)

# --- root empty at origin; object parented under it (so we can scale-to-fit) ---
root = bpy.data.objects.new(root_name, None)
root.empty_display_size = 0.05
sc.collection.objects.link(root)
o.parent = root

# --- bake the motion as per-frame keyframes (seamless 0..1 phase loop) ---
N = int(P["frames"]); motion = P["motion"]; TAU = 2.0 * math.pi
amp = float(P["apex"])

# New keyframes default to LINEAR so constant motions stay constant and bounces
# don't Bezier-overshoot through the floor. We set interpolation at INSERT time
# because Blender 5.1 removed Action.fcurves for slotted actions (post-processing
# is unreliable). Save/restore so the user's live preferences are untouched.
_ked = bpy.context.preferences.edit
_saved_interp = _ked.keyframe_new_interpolation_type
_ked.keyframe_new_interpolation_type = 'LINEAR'

def kloc(fr, x, y, z):
    o.location = (x, y, z); o.keyframe_insert("location", frame=fr)

def krot(fr, rx, ry, rz):
    o.rotation_euler = (rx, ry, rz); o.keyframe_insert("rotation_euler", frame=fr)

if motion == "bounce":
    nb = int(P.get("bounces", 2))
    for f in range(1, N + 1):
        p = (f - 1) / N
        frac = (p * nb) % 1.0
        z = foot + amp * (1.0 - (2.0 * frac - 1.0) ** 2)
        kloc(f, 0.0, 0.0, z)
elif motion == "roll":
    A = min(0.30, amp); r = max(1e-4, foot)
    for f in range(1, N + 1):
        p = (f - 1) / N
        x = A * math.sin(TAU * p)
        kloc(f, x, 0.0, foot)
        krot(f, 0.0, -x / r, 0.0)
elif motion == "orbit":
    R = min(0.30, amp)
    for f in range(1, N + 1):
        p = (f - 1) / N
        kloc(f, R * math.cos(TAU * p), R * math.sin(TAU * p), foot)
elif motion == "hover":
    A = min(0.12, amp)
    for f in range(1, N + 1):
        p = (f - 1) / N
        kloc(f, 0.0, 0.0, foot + 0.10 + A * math.sin(TAU * p))
    krot(1, 0, 0, 0); krot(N, 0, 0, TAU)
elif motion == "pulse":
    for f in range(1, N + 1):
        p = (f - 1) / N
        s = 1.0 + 0.18 * math.sin(TAU * p)
        kloc(f, 0.0, 0.0, foot)
        o.scale = (s, s, s); o.keyframe_insert("scale", frame=f)
elif motion == "wobble":
    A = math.radians(18.0)
    for f in range(1, N + 1):
        p = (f - 1) / N
        kloc(f, 0.0, 0.0, foot)
        krot(f, A * math.sin(TAU * p), A * math.cos(TAU * p), 0.0)
else:  # spin (default): turntable about Z, constant speed, seamless
    kloc(1, 0.0, 0.0, foot)
    krot(1, 0, 0, 0); krot(N, 0, 0, TAU)

# restore the user's keyframe-interpolation preference
_ked.keyframe_new_interpolation_type = _saved_interp

# defensive: coerce keys to LINEAR via a version-safe fcurve walk (no-op if the
# preference above already did it). Blender 5.1 slotted actions expose fcurves
# under layers/strips/channelbags; older Blenders expose Action.fcurves directly.
# Wrapped so any future API shift can never fail the build.
try:
    def _iter_fcurves(act):
        legacy = getattr(act, "fcurves", None)
        if legacy is not None:
            return list(legacy)
        out = []
        for layer in getattr(act, "layers", []):
            for strip in getattr(layer, "strips", []):
                for cbag in getattr(strip, "channelbags", []):
                    out.extend(getattr(cbag, "fcurves", []))
        return out
    if o.animation_data and o.animation_data.action:
        for fc in _iter_fcurves(o.animation_data.action):
            for kp in fc.keyframe_points:
                kp.interpolation = 'LINEAR'
except Exception:
    pass

result = {"object": o.name, "root": root.name, "motion": motion, "shape": shape,
          "verts": len(o.data.vertices)}
"""

_EXPORT = r"""
import pathlib
name = P["name"]; root_name = P["root_name"]; N = int(P["frames"])
o = bpy.data.objects.get(name); root = bpy.data.objects.get(root_name)
sc = bpy.context.scene
if o is None:
    result = {"exported": False, "error": "object %s missing" % name}
else:
    def animated_bbox():
        lo = [1e18, 1e18, 1e18]; hi = [-1e18, -1e18, -1e18]
        for f in range(1, N + 1):
            sc.frame_set(f); bpy.context.view_layer.update()
            for c in o.bound_box:
                w = o.matrix_world @ Vector(c)
                for i in range(3):
                    if w[i] < lo[i]: lo[i] = w[i]
                    if w[i] > hi[i]: hi[i] = w[i]
        return lo, hi
    lo, hi = animated_bbox()
    dims = [hi[i] - lo[i] for i in range(3)]; mx = max(dims)
    target = float(P.get("max_dim_target", 0.9))
    if mx > target and root is not None and mx > 0:
        s = target / mx
        root.scale = (s, s, s); bpy.context.view_layer.update()
        lo, hi = animated_bbox()
        dims = [hi[i] - lo[i] for i in range(3)]; mx = max(dims)
    # Blender Z-up -> Y-up (matches studio blender_to_yup: [x, z, -y])
    yup_min = [round(lo[0], 4), round(lo[2], 4), round(-hi[1], 4)]
    yup_max = [round(hi[0], 4), round(hi[2], 4), round(-lo[1], 4)]

    sc.frame_set(1)
    # ---- USDZ export: EXACT studio/blender/build_studio.py::_export_usdz settings ----
    rna = set(bpy.ops.wm.usd_export.get_rna_type().properties.keys())
    desired = {
        "filepath": P["usdz_path"],
        "selected_objects_only": False,
        "export_animation": True,
        "export_uvmaps": True,
        "export_normals": True,
        "export_materials": True,
        "export_meshes": True,
        "generate_preview_surface": True,
        "convert_orientation": True,
        "export_global_forward_selection": "NEGATIVE_Z",
        "export_global_up_selection": "Y",
        "meters_per_unit": 1.0,
        "root_prim_path": "/Scene",
        "overwrite_textures": True,
    }
    kwargs = {k: v for k, v in desired.items() if k in rna}
    kwargs["filepath"] = P["usdz_path"]
    ok = False; err = None
    try:
        bpy.ops.wm.usd_export(**kwargs); ok = True
    except Exception:
        import traceback as _tb
        err = _tb.format_exc()
    exists = pathlib.Path(P["usdz_path"]).exists()
    result = {"exported": bool(ok and exists), "error": err,
              "bbox_yup": {"min": yup_min, "max": yup_max},
              "max_dim": round(mx, 4), "frames": N, "fps": int(P["fps"])}
"""


def _code(spec: dict, body: str) -> str:
    return _PRELUDE % json.dumps(spec) + body


def generate(prompt: str, job_id: str, out_dir, on_stage=lambda s: None) -> dict:
    """Model + animate + export. Returns artifact names + bbox. Raises on failure."""
    if bridge.ping() is None:
        raise bridge.BridgeError(
            "Blender MCP bridge not reachable on localhost:9876 - open Blender and "
            "make sure the 'MCP' add-on server is running."
        )

    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    usdz_path = out_dir / f"{job_id}.usdz"
    spec = parse_prompt(prompt)
    # forward slashes: valid for Blender on Windows and avoids backslash escaping
    spec["usdz_path"] = str(usdz_path).replace("\\", "/")

    on_stage("modeling")
    bridge.run_code(_code(spec, _BUILD_ANIMATE), timeout=120.0)

    on_stage("exporting")
    res = bridge.run_code(_code(spec, _EXPORT), timeout=180.0)
    if not res.get("exported"):
        raise bridge.BridgeError(f"USDZ export failed: {res.get('error')}")

    # --- metadata (server-side), reusing the studio metadata shape ---
    meta = {
        "sceneId": job_id,
        "title": prompt.strip() or "Generated object",
        "prompt": prompt,
        "generator": "spatail.generative.v0.1",
        "frame": "y_up_m",
        "mode": "ar_single",
        "usdz": f"{job_id}.usdz",
        "animation": {"clip": "gen_demo", "fps": res["fps"], "frames": res["frames"],
                      "seconds": round(res["frames"] / res["fps"], 2), "loop": True},
        "bbox_yup_m": res["bbox_yup"],
        "max_dim_m": res["max_dim"],
        "spec": {k: spec[k] for k in ("shape", "color_name", "motion", "size")},
    }
    meta_path = out_dir / f"{job_id}_metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    on_stage("ready")

    return {
        "usdz_name": usdz_path.name,
        "metadata_name": meta_path.name,
        "bbox_yup": res["bbox_yup"],
        "max_dim": res["max_dim"],
        "spec": meta["spec"],
    }
