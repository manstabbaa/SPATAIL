"""build_studio.py - headless Blender builder for the SPATAIL Studio.

Run by run.py via:
    blender --background --factory-startup --python build_studio.py -- <scene_spec.json> <out_dir>

It performs two studio roles in one deterministic pass:
  * Artist   - builds + animates each beat's demo from the scene spec, composing
               RECOGNIZABLE real-world objects from studio/blender/realworld.py
               (an air-hockey table, a wooden ramp, lab carts) and baking the
               real physics equations to keyframes, so motion is correct AND
               exports cleanly to glTF. There is NO primitive-fallback path: an
               unknown demo is a hard error, never a grey box.
  * Developer (geometry side) - stages every exhibit on a comfort-driven arc
               using studio/xr_design.py, so layout follows the Apple/Magic Leap
               rules instead of hand-placed guesses.

Outputs into <out_dir>:
  * studio.glb            - room + all exhibits + one looping animation clip
  * studio_metadata.json  - per-beat bbox / anchor / placement reasoning, for
                            the contract builder (the Developer's handoff note).

Frame: Blender-native metres, +Z up, +Y forward. Exported +Y-up for three.js.
"""
import bpy
import bmesh
import json
import math
import sys
from pathlib import Path
from mathutils import Vector

# --- args + xr_design import --------------------------------------------------
_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
SCENE_SPEC = Path(_argv[0])
OUT_DIR = Path(_argv[1])
OUT_DIR.mkdir(parents=True, exist_ok=True)

STUDIO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO_DIR))
sys.path.insert(0, str(STUDIO_DIR / "blender"))
import xr_design as xr  # noqa: E402
import realworld as rw  # noqa: E402

FPS = 30
CYCLE = 120                  # frames -> 4.0 s seamless loop
G = 9.81
SLOWMO_DOWN_FRAC = 0.82      # fraction of the cycle the accelerating phase uses


# --- low-level geometry (bmesh: no operator/context dependence) ---------------

def _mesh_box(name, sx, sy, sz):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((sx, sy, sz)), verts=bm.verts)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def _mesh_cylinder(name, radius, depth, axis="z", seg=40):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=seg,
                          radius1=radius, radius2=radius, depth=depth)
    if axis == "x":
        bmesh.ops.rotate(bm, verts=bm.verts,
                         matrix=_rot_matrix(math.radians(90), "Y"))
    elif axis == "y":
        bmesh.ops.rotate(bm, verts=bm.verts,
                         matrix=_rot_matrix(math.radians(90), "X"))
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def _mesh_sphere(name, radius, subd=3):
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subd, radius=radius)
    me = bpy.data.meshes.new(name)
    for f in bm.faces:
        f.smooth = True
    bm.to_mesh(me)
    bm.free()
    return me


def _rot_matrix(angle, axis):
    from mathutils import Matrix
    return Matrix.Rotation(angle, 3, axis)


_MATS = {}


def _material(name, rgba, rough=0.6, metal=0.0, emit=None, emit_strength=1.0):
    if name in _MATS:
        return _MATS[name]
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = rgba
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = rough
        if "Metallic" in bsdf.inputs:
            bsdf.inputs["Metallic"].default_value = metal
        if emit and "Emission Color" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = emit
            if "Emission Strength" in bsdf.inputs:
                bsdf.inputs["Emission Strength"].default_value = emit_strength
    mat.diffuse_color = rgba
    _MATS[name] = mat
    return mat


def _obj(name, mesh, parent=None, location=(0, 0, 0), rotation=(0, 0, 0),
         material=None):
    o = bpy.data.objects.new(name, mesh)
    if material:
        o.data.materials.append(material)
    bpy.context.scene.collection.objects.link(o)
    if parent is not None:
        o.parent = parent          # data-API parenting keeps local = basis
    o.location = Vector(location)
    o.rotation_euler = Vector(rotation)
    return o


def _empty(name, location=(0, 0, 0), rotation=(0, 0, 0)):
    e = bpy.data.objects.new(name, None)
    e.empty_display_size = 0.05
    bpy.context.scene.collection.objects.link(e)
    e.location = Vector(location)
    e.rotation_euler = Vector(rotation)
    return e


def _key_loc(o, frame, loc):
    o.location = Vector(loc)
    o.keyframe_insert("location", frame=frame)


def _key_rot(o, frame, rot):
    o.rotation_euler = Vector(rot)
    o.keyframe_insert("rotation_euler", frame=frame)


# --- palette ------------------------------------------------------------------
COL = {
    "floor": (0.82, 0.80, 0.75, 1.0),
    "wall": (0.88, 0.87, 0.83, 1.0),
    "pad": (0.16, 0.18, 0.22, 1.0),
    "track": (0.55, 0.57, 0.62, 1.0),
    "ramp": (0.60, 0.62, 0.67, 1.0),
    "ball": (0.93, 0.36, 0.24, 1.0),
    "puck": (0.20, 0.62, 0.86, 1.0),
    "cart_a": (0.36, 0.78, 0.50, 1.0),
    "cart_b": (0.78, 0.55, 0.30, 1.0),
    "spring": (0.95, 0.82, 0.25, 1.0),
}


# --- room ---------------------------------------------------------------------

def build_room():
    floor_far = xr.ROOM_D - 1.5            # floor spans y in [-1.5, far]
    cy = (floor_far + -1.5) / 2.0
    _obj("studio_floor", _mesh_box("studio_floor", xr.ROOM_W, xr.ROOM_D, 0.04),
         location=(0, cy, -0.02), material=_material("m_floor", COL["floor"], rough=0.9))
    # soft back wall behind the content, facing the user
    _obj("studio_backwall", _mesh_box("studio_backwall", xr.ROOM_W, 0.04, xr.ROOM_H),
         location=(0, floor_far, xr.ROOM_H / 2.0),
         material=_material("m_wall", COL["wall"], rough=0.95))
    return {"floor_far": floor_far}


# --- demos: each composes REAL-WORLD objects (realworld.py) + bakes real motion
#     and returns {root, footprint_w, min_z, max_z}. No primitive fallback. -----

def demo_constant_velocity_puck(root, params):
    """Law 1 (Inertia) - a puck glides at constant speed across an AIR-HOCKEY
    TABLE. Frictionless surface => it never slows: that's inertia."""
    L = float(params.get("surface_len_m", 0.9))
    table = rw.air_hockey_table(root, "law1", length=L, width=0.46)
    p = rw.puck(root, "law1", radius=0.045, surface_z=table["surface_z"])
    puck = p["obj"]
    z = p["z"]
    travel = L / 2 - p["radius"] - 0.03
    mid = CYCLE // 2
    _key_loc(puck, 1, (-travel, 0, z))
    _key_loc(puck, mid, (travel, 0, z))
    _key_loc(puck, CYCLE, (-travel, 0, z))
    return {"footprint_w": table["footprint_w"], "min_z": 0.0, "max_z": z + p["radius"]}


def demo_inclined_plane(root, params):
    """Law 2 (F = ma) - a steel ball rolls down a WOODEN RAMP on a stand. Gravity
    along the slope (g.sin0) accelerates it: distance grows as 1/2 a t^2."""
    L = float(params.get("ramp_len_m", 0.9))
    angle = float(params.get("angle_deg", 22))
    r = float(params.get("ball_radius_m", 0.05))
    ramp = rw.wooden_ramp(root, "law2", length=L, angle_deg=angle)
    b = rw.ball(root, "law2", radius=r)
    ball = b["obj"]
    theta = ramp["theta"]
    down, normal, top = ramp["down"], ramp["normal"], ramp["top"]
    top = top + normal * (ramp["plank_t"] / 2 + r)      # rest on the plank surface
    a = G * math.sin(theta)
    t_bottom = math.sqrt(2 * L / a)
    f_down = int(CYCLE * SLOWMO_DOWN_FRAC)
    min_z, max_z = 1e9, -1e9
    for f in range(1, f_down + 1):
        tau = (f - 1) / (f_down - 1) * t_bottom
        s = min(0.5 * a * tau * tau, L)
        pos = top + down * s
        _key_loc(ball, f, (pos.x, pos.y, pos.z))
        _key_rot(ball, f, (0.0, -s / r, 0.0))           # rolling without slipping
        min_z = min(min_z, pos.z - r); max_z = max(max_z, pos.z + r)
    for k, f in enumerate(range(f_down + 1, CYCLE + 1)):
        g = k / (CYCLE - f_down)
        pos = top + down * (L * (1 - g))
        _key_loc(ball, f, (pos.x, pos.y, pos.z))
        _key_rot(ball, f, (0.0, -(L * (1 - g)) / r, 0.0))
    return {"footprint_w": ramp["footprint_w"],
            "min_z": min(min_z, ramp["min_z"]), "max_z": max(max_z, ramp["max_z"])}


def demo_spring_carts(root, params):
    """Law 3 (Action-Reaction) - a COIL SPRING between two LAB CARTS releases and
    shoves them apart. The push is equal both ways, so the lighter cart (left)
    travels `mass_ratio` x farther and its wheels spin proportionally faster."""
    ratio = float(params.get("mass_ratio", 2.0))       # m_heavy / m_light
    cart_a = rw.lab_cart(root, "law3a", color="cart_blue")   # light, left
    cart_b = rw.lab_cart(root, "law3b", color="cart_red")    # heavy, right
    ra, rb = cart_a["root"], cart_b["root"]
    zc = 0.0                                            # carts already sit on wheels
    spring = rw.coil_spring(root, "law3", length=0.06, coil_r=0.028, wire_r=0.005, turns=6)
    sp = spring["obj"]
    spring_z = cart_a["wheel_r"] + 0.03
    gap0 = cart_a["deck"] / 2 + 0.03
    ax0, bx0 = -gap0, gap0
    dB = 0.11
    dA = dB * ratio
    apart = int(CYCLE * 0.46)
    hold = int(CYCLE * 0.12)
    # cart bodies move apart then return (perfect loop)
    _key_loc(ra, 1, (ax0, 0, zc));                _key_loc(rb, 1, (bx0, 0, zc))
    _key_loc(ra, apart, (ax0 - dA, 0, zc));       _key_loc(rb, apart, (bx0 + dB, 0, zc))
    _key_loc(ra, apart + hold, (ax0 - dA, 0, zc)); _key_loc(rb, apart + hold, (bx0 + dB, 0, zc))
    _key_loc(ra, CYCLE, (ax0, 0, zc));            _key_loc(rb, CYCLE, (bx0, 0, zc))
    # wheels roll: angle = distance / wheel_r, lighter cart spins ratio x more
    for w in cart_a["wheels"]:
        _key_rot(w, 1, (0, 0, 0)); _key_rot(w, apart, (0, -dA / cart_a["wheel_r"], 0))
        _key_rot(w, apart + hold, (0, -dA / cart_a["wheel_r"], 0)); _key_rot(w, CYCLE, (0, 0, 0))
    for w in cart_b["wheels"]:
        _key_rot(w, 1, (0, 0, 0)); _key_rot(w, apart, (0, dB / cart_b["wheel_r"], 0))
        _key_rot(w, apart + hold, (0, dB / cart_b["wheel_r"], 0)); _key_rot(w, CYCLE, (0, 0, 0))
    # spring sits centred, compressed at rest then releases (scale along its X axis)
    sp.location = (0, 0, spring_z); sp.keyframe_insert("location", frame=1)
    sp.scale = (0.5, 1, 1); sp.keyframe_insert("scale", frame=1)
    sp.scale = (1.0, 1, 1); sp.keyframe_insert("scale", frame=int(apart * 0.4))
    sp.scale = (1.0, 1, 1); sp.keyframe_insert("scale", frame=apart + hold)
    sp.scale = (0.5, 1, 1); sp.keyframe_insert("scale", frame=CYCLE)
    return {"footprint_w": gap0 * 2 + dA + dB + cart_a["deck"],
            "min_z": 0.0, "max_z": max(cart_a["top_z"], cart_b["top_z"]) + 0.05}


DEMOS = {
    "constant_velocity_puck": demo_constant_velocity_puck,
    "inclined_plane": demo_inclined_plane,
    "spring_carts": demo_spring_carts,
}


# --- staging (Developer): comfort-driven arc placement ------------------------

def stage_beats(beats, built):
    """Place each exhibit so neighbours never overlap, on an arc curved toward
    the user, baseline dropped below eye level. Returns placement records."""
    n = len(beats)
    maxw = max(b["footprint_w"] for b in built) if built else 0.4
    gap = 0.18
    # pick a distance in the multi-panel reading band; widen arc to clear widths
    D = xr.FOCAL_PLANE_M if n == 1 else xr.READ_FAR_M
    step = 0.0 if n == 1 else 2 * math.asin(min(0.999, (maxw + gap) / (2 * D)))
    spread = step * (n - 1)
    spread_max = math.radians(64)
    if spread > spread_max and n > 1:
        # too wide for comfort -> push exhibits further out so the fan tightens
        step = spread_max / (n - 1)
        D = min(xr.FAR_MAX_M, (maxw + gap) / (2 * math.sin(step / 2)))
        spread = spread_max
    z = xr.baseline_height(D)
    records = []
    for i, (beat, geo) in enumerate(zip(beats, built)):
        ang = 0.0 if n == 1 else (-spread / 2 + step * i)
        x = D * math.sin(ang)
        y = D * math.cos(ang)
        root = geo["root"]
        root.location = Vector((x, y, z))
        root.rotation_euler = Vector((0, 0, xr.yaw_to_face_user(x, y)))
        # label floats above the exhibit, at reading distance, near eye level
        label_anchor = (x, y, xr.EYE_HEIGHT_M - 0.05 + geo["max_z"])
        records.append({
            "id": beat["id"],
            "anchor_blender": [round(x, 4), round(y, 4), round(z, 4)],
            "label_anchor_blender": [round(label_anchor[0], 4),
                                     round(label_anchor[1], 4),
                                     round(label_anchor[2], 4)],
            "yaw_deg": round(math.degrees(xr.yaw_to_face_user(x, y)), 2),
            "footprint_w": round(geo["footprint_w"], 4),
            "distance_m": round(D, 3),
            "in_comfort_cone": xr.in_comfort_cone((x, y, z)),
        })
    return {
        "distance_m": round(D, 3),
        "spread_deg": round(math.degrees(spread), 2),
        "baseline_z": round(z, 4),
        "records": records,
        "reasoning": (
            f"{n} exhibit(s), widest {maxw:.2f} m. Placed on a {math.degrees(spread):.0f}deg "
            f"arc at {D:.2f} m (curved toward the user, equidistant) so neighbours "
            f"clear by >={gap:.2f} m; baseline dropped {xr.GAZE_DOWN_DEG:.0f}deg below "
            f"eye to {z:.2f} m so nothing is craned at."
        ),
    }


def blender_to_yup(p):
    """Blender (x,y,z) -> three.js Y-up (matches the +Y-up GLB export)."""
    return [round(p[0], 4), round(p[2], 4), round(-p[1], 4)]


# --- main ---------------------------------------------------------------------

def main():
    spec = json.loads(SCENE_SPEC.read_text(encoding="utf-8"))
    beats = spec["beats"]

    # clean slate + linear keyframes (constant velocity must stay constant)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.edit.keyframe_new_interpolation_type = 'LINEAR'
    scene = bpy.context.scene
    scene.render.fps = FPS
    scene.frame_start = 1
    scene.frame_end = CYCLE

    build_room()

    built = []
    for beat in beats:
        fn = DEMOS.get(beat["demo"])
        if not fn:
            # NO primitive fallback: a demo the Artist hasn't built is a hard
            # failure, not a grey box. The Artist must add demo_<name> to DEMOS.
            raise SystemExit(
                f"[build_studio] FATAL: no Blender builder for demo "
                f"{beat['demo']!r} (beat {beat['id']!r}). Known demos: "
                f"{sorted(DEMOS)}. Build it in studio/blender/ — never ship a box.")
        root = _empty(f"beat_{beat['id']}_root")
        geo = fn(root, beat.get("params", {}))
        geo["root"] = root
        if geo.get("footprint_w", 0) <= 0:
            raise SystemExit(f"[build_studio] FATAL: demo {beat['demo']!r} built "
                             f"no geometry (footprint 0).")
        built.append(geo)

    staging = stage_beats(beats, built)

    # global bbox at rest
    scene.frame_set(1)
    bpy.context.view_layer.update()
    lo = Vector((1e9,) * 3)
    hi = Vector((-1e9,) * 3)
    for o in scene.objects:
        if o.type != "MESH":
            continue
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))

    glb_path = OUT_DIR / "studio.glb"
    _export_glb(glb_path)

    # metadata: the Developer's handoff note
    meta = {
        "sceneId": spec.get("sceneId"),
        "title": spec.get("title"),
        "glb": "studio/out/studio.glb",
        "frame": "y_up_m",
        "animation": {"clip": "studio_demo", "fps": FPS, "frames": CYCLE,
                      "seconds": round(CYCLE / FPS, 2), "loop": True},
        "room": {"w": xr.ROOM_W, "d": xr.ROOM_D, "h": xr.ROOM_H},
        "user": {"eye_height_m": xr.EYE_HEIGHT_M},
        "bbox_yup_m": {"min": blender_to_yup(lo), "max": blender_to_yup(hi)},
        "staging": {
            "distance_m": staging["distance_m"],
            "spread_deg": staging["spread_deg"],
            "reasoning": staging["reasoning"],
        },
        "beats": [],
    }
    rec_by_id = {r["id"]: r for r in staging["records"]}
    for beat in beats:
        r = rec_by_id.get(beat["id"], {})
        meta["beats"].append({
            "id": beat["id"],
            "law": beat.get("law"),
            "subtitle": beat.get("subtitle"),
            "title": beat.get("title"),
            "narration": beat.get("narration"),
            "demo": beat.get("demo"),
            "anchor_yup_m": blender_to_yup(r.get("anchor_blender", [0, 0, 0])),
            "label_anchor_yup_m": blender_to_yup(r.get("label_anchor_blender", [0, 0, 0])),
            "distance_m": r.get("distance_m"),
            "in_comfort_cone": r.get("in_comfort_cone"),
        })

    (OUT_DIR / "studio_metadata.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")
    print(f"[build_studio] wrote {glb_path}")
    print(f"[build_studio] wrote {OUT_DIR / 'studio_metadata.json'}")
    print(f"[build_studio] staging: {staging['reasoning']}")


def _export_glb(path):
    scene = bpy.context.scene
    scene.frame_set(1)
    try:
        bpy.ops.preferences.addon_enable(module="io_scene_gltf2")
    except Exception:
        pass
    common = dict(filepath=str(path), export_format="GLB", export_yup=True,
                  export_animations=True, export_apply=False)
    # one combined clip from the scene timeline if supported
    for extra in (dict(export_animation_mode="SCENE"), {}):
        try:
            bpy.ops.export_scene.gltf(**common, **extra)
            return
        except TypeError:
            continue
    raise RuntimeError("glTF export failed")


main()
