"""grow_compare.py — slice 2: a plant GROWTH rig built TWO WAYS, to judge.

    blender --background --python studio/meshy/grow_compare.py -- <mode> <out_dir>
    mode ∈ {keyframed, geonodes}

Both build the SAME deterministic plant (pot + stem + 5 leaves) and bake a single
"grow" clip (seedling → full), export a shippable GLB + USDZ, and render a 3-frame
filmstrip (sprout / mid / full) so the two approaches can be compared side by side.

  keyframed : the plant stays SEPARATE part objects; each part's transform (scale) is
              hand-keyframed in staged steps. Exports as plain node TRS animation —
              trivial to ship, plays natively everywhere. Coarser: a leaf pops in as a
              whole. This is "authoring simplicity".

  geonodes  : the plant is ONE mesh carrying a per-vertex `growth_order` attribute; a
              Geometry Nodes graph scales each point in from the stem base by a single
              "Factor" input (continuous, parametric — works for any plant). Because GN
              can't be shipped live, the factor sweep is BAKED to shape keys (constant
              topology) and exported as morph targets. Smoother + regenerable, but heavier
              and needs the bake. This is "procedural power".

The clip is named "grow" (loop=False) so the runtime/brief can call it by name.
"""
import sys
import math
import json
from pathlib import Path

import bpy
from mathutils import Vector

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "library"))
import bake_assets as BA  # noqa: E402  (proven exporters; we add animation kwargs below)

FPS = 30
FRAMES = 90                 # 3 s grow
N_LEAVES = 5
POT_TOP_Z = 0.10            # soil height
STEM_H = 0.34
LEAF_BASE_Z = 0.12         # first leaf attaches just above the soil


# ── materials ────────────────────────────────────────────────────────────────

def _mat(name, rgba, rough=0.6):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    if b:
        b.inputs["Base Color"].default_value = rgba
        if "Roughness" in b.inputs:
            b.inputs["Roughness"].default_value = rough
    m.diffuse_color = rgba
    return m


# ── scene ────────────────────────────────────────────────────────────────────

def _clear():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for blk in (bpy.data.meshes, bpy.data.materials, bpy.data.images,
                bpy.data.node_groups, bpy.data.objects):
        for b in list(blk):
            if getattr(b, "users", 0) == 0:
                try:
                    blk.remove(b)
                except Exception:
                    pass


def _leaf_z(i):
    """Height up the stem where leaf i attaches (evenly spaced, alternating sides)."""
    t = (i + 0.5) / N_LEAVES
    return LEAF_BASE_Z + t * (STEM_H - 0.04)


def build_plant():
    """Deterministic plant: pot, stem, N leaves. Origins set at each part's GROWTH
    base (stem bottom, leaf attachment) so a scale of 0 = 'not yet grown'."""
    green = _mat("Leaf", (0.22, 0.55, 0.16, 1.0), 0.5)
    stem_m = _mat("Stem", (0.30, 0.50, 0.18, 1.0), 0.5)
    pot_m = _mat("Pot", (0.55, 0.30, 0.20, 1.0), 0.7)

    # pot (frustum) — does not grow
    bpy.ops.mesh.primitive_cone_add(vertices=28, radius1=0.085, radius2=0.065,
                                    depth=POT_TOP_Z, location=(0, 0, POT_TOP_Z / 2))
    pot = bpy.context.active_object
    pot.name = "pot"
    pot.data.materials.append(pot_m)

    # stem — a tapered cylinder, origin at its BOTTOM so scale.z grows it upward
    bpy.ops.mesh.primitive_cone_add(vertices=12, radius1=0.012, radius2=0.005,
                                    depth=STEM_H, location=(0, 0, POT_TOP_Z + STEM_H / 2))
    stem = bpy.context.active_object
    stem.name = "stem"
    stem.data.materials.append(stem_m)
    _set_origin(stem, Vector((0, 0, POT_TOP_Z)))

    leaves = []
    for i in range(N_LEAVES):
        z = _leaf_z(i)
        yaw = math.radians((i * 137.5) % 360.0)         # phyllotaxis spread
        tilt = math.radians(52)                          # lean out from the stem
        # a pointed blade: a flattened cone (tip up), base sitting at world z=0
        bpy.ops.mesh.primitive_cone_add(vertices=10, radius1=0.024, radius2=0.0,
                                        depth=0.085, location=(0, 0, 0.0425))
        leaf = bpy.context.active_object
        leaf.name = f"leaf_{i+1}"
        leaf.scale = (0.22, 1.0, 1.0)                    # thin blade in local X
        bpy.ops.object.transform_apply(scale=True)
        _set_origin(leaf, Vector((0, 0, 0.0)))           # origin at the blade BASE
        # tilt out, spin around the stem, attach the base at the stem surface
        leaf.rotation_euler = (tilt, 0.0, yaw)
        stem_r = 0.011 * (1 - (z - POT_TOP_Z) / STEM_H) + 0.004
        leaf.location = (math.sin(yaw) * stem_r, math.cos(yaw) * stem_r, z)
        leaf.data.materials.append(green)
        leaves.append(leaf)

    return {"pot": pot, "stem": stem, "leaves": leaves}


def _set_origin(obj, world_point):
    """Move an object's origin to a world point without moving the geometry."""
    bpy.context.view_layer.objects.active = obj
    cur = bpy.context.scene.cursor.location.copy()
    bpy.context.scene.cursor.location = world_point
    bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
    bpy.context.scene.cursor.location = cur


# ── mode A: keyframed TRS ─────────────────────────────────────────────────────

def _all_fcurves(obj):
    """F-curves for an object, across Blender's legacy flat action AND the 4.4+/5.x
    layered/slotted action API."""
    ad = obj.animation_data
    if not ad or not ad.action:
        return []
    act = ad.action
    legacy = getattr(act, "fcurves", None)
    if legacy and len(legacy):
        return list(legacy)
    out = []
    for layer in getattr(act, "layers", []):
        for strip in getattr(layer, "strips", []):
            cbags = getattr(strip, "channelbags", None)
            if cbags is not None:
                for cb in cbags:
                    out.extend(cb.fcurves)
            else:
                for slot in getattr(act, "slots", []):
                    try:
                        cb = strip.channelbag(slot)
                        if cb:
                            out.extend(cb.fcurves)
                    except Exception:
                        pass
    return out


def _set_interp(obj, interp="BEZIER"):
    for fc in _all_fcurves(obj):
        for kp in fc.keyframe_points:
            kp.interpolation = interp


def _key(obj, frame, scale):
    obj.scale = scale
    obj.keyframe_insert("scale", frame=frame)


def mode_keyframed(parts, fps=FPS, n=FRAMES):
    """Stage the growth as per-part scale keyframes: stem extends, then each leaf
    unfurls in turn. Plain TRS animation — exports + plays natively."""
    stem = parts["stem"]
    _key(stem, 1, (1, 1, 0.02))
    _key(stem, int(n * 0.55), (1, 1, 1.0))
    _key(stem, n, (1, 1, 1.0))
    for i, leaf in enumerate(parts["leaves"]):
        start = int(n * (0.12 + 0.62 * (i / max(N_LEAVES - 1, 1))))
        end = min(start + int(n * 0.22), n)
        _key(leaf, 1, (0, 0, 0))
        _key(leaf, start, (0, 0, 0))
        _key(leaf, end, (1, 1, 1))
        _key(leaf, n, (1, 1, 1))
    # ease the curves
    for obj in [stem, *parts["leaves"]]:
        _set_interp(obj, "BEZIER")
    return [{"name": "grow", "role": "grow", "start": 1, "end": n, "fps": fps, "loop": False}]


# ── mode B: geometry-nodes growth → bake to shape keys ────────────────────────

def _growth_order_attr(obj):
    """Per-vertex growth_order ∈ [0,1] = normalized height above the soil. Lower
    geometry grows first — the plant rises from the base."""
    me = obj.data
    zs = [(obj.matrix_world @ v.co).z for v in me.vertices]
    z0, z1 = POT_TOP_Z, max(zs + [POT_TOP_Z + 0.01])
    attr = me.attributes.get("growth_order") or me.attributes.new("growth_order", "FLOAT", "POINT")
    span = max(z1 - z0, 1e-4)
    for i, v in enumerate(me.vertices):
        wz = (obj.matrix_world @ v.co).z
        attr.data[i].value = min(max((wz - z0) / span, 0.0), 1.0)


def _build_grow_nodegroup():
    """A GN graph: new_pos = base + (pos - base) * smoothstep(Factor - growth_order).
    Pure Set Position (constant topology) so it bakes to shape keys."""
    ng = bpy.data.node_groups.new("GrowGN", "GeometryNodeTree")
    iface = ng.interface
    iface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    iface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    s_fac = iface.new_socket("Factor", in_out="INPUT", socket_type="NodeSocketFloat")
    s_fac.default_value = 0.0
    s_fac.min_value = 0.0
    s_fac.max_value = 1.0

    nodes, links = ng.nodes, ng.links
    gin = nodes.new("NodeGroupInput")
    gout = nodes.new("NodeGroupOutput")

    named = nodes.new("GeometryNodeInputNamedAttribute")
    named.data_type = "FLOAT"
    named.inputs["Name"].default_value = "growth_order"

    # progress = Factor - growth_order
    sub = nodes.new("ShaderNodeMath"); sub.operation = "SUBTRACT"
    links.new(gin.outputs["Factor"], sub.inputs[0])
    links.new(named.outputs["Attribute"], sub.inputs[1])
    # scale = smoothstep(0, band, progress)  → Map Range SMOOTHSTEP, clamped
    mr = nodes.new("ShaderNodeMapRange"); mr.interpolation_type = "SMOOTHSTEP"
    mr.inputs["From Min"].default_value = 0.0
    mr.inputs["From Max"].default_value = 0.22
    mr.inputs["To Min"].default_value = 0.0
    mr.inputs["To Max"].default_value = 1.0
    links.new(sub.outputs["Value"], mr.inputs["Value"])

    # base point on the stem axis at the soil: (0,0,POT_TOP_Z)
    base = nodes.new("FunctionNodeInputVector"); base.vector = (0.0, 0.0, POT_TOP_Z)
    pos = nodes.new("GeometryNodeInputPosition")
    delta = nodes.new("ShaderNodeVectorMath"); delta.operation = "SUBTRACT"
    links.new(pos.outputs["Position"], delta.inputs[0])
    links.new(base.outputs["Vector"], delta.inputs[1])
    scaled = nodes.new("ShaderNodeVectorMath"); scaled.operation = "SCALE"
    links.new(delta.outputs["Vector"], scaled.inputs[0])
    links.new(mr.outputs["Result"], scaled.inputs["Scale"])
    newpos = nodes.new("ShaderNodeVectorMath"); newpos.operation = "ADD"
    links.new(base.outputs["Vector"], newpos.inputs[0])
    links.new(scaled.outputs["Vector"], newpos.inputs[1])

    setpos = nodes.new("GeometryNodeSetPosition")
    links.new(gin.outputs["Geometry"], setpos.inputs["Geometry"])
    links.new(newpos.outputs["Vector"], setpos.inputs["Position"])
    links.new(setpos.outputs["Geometry"], gout.inputs["Geometry"])
    return ng


def mode_geonodes(parts, fps=FPS, n=FRAMES, n_keys=None):
    """Join the growable parts into one mesh, drive growth by a GN Factor, then BAKE
    the factor sweep to shape keys (constant topology) and animate their weights.

    n_keys: how many morph poses to sample. None/≥n → dense (one per frame, the pure
    procedural showcase). A small value (e.g. 8) is the HYBRID ship path: author the
    continuous GN motion, bake only a handful of poses, and let the runtime interpolate
    their weights — keeping the organic rise at a fraction of the payload."""
    # join stem + leaves into one growable mesh (pot stays separate, static)
    grow_parts = [parts["stem"], *parts["leaves"]]
    bpy.ops.object.select_all(action="DESELECT")
    for o in grow_parts:
        o.select_set(True)
    bpy.context.view_layer.objects.active = parts["stem"]
    bpy.ops.object.join()
    plant = bpy.context.view_layer.objects.active
    plant.name = "plant"
    # bake any per-part scale into the mesh so the GN sees the full plant
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    _growth_order_attr(plant)

    ng = _build_grow_nodegroup()
    mod = plant.modifiers.new("Grow", "NODES")
    mod.node_group = ng
    fac_id = ng.interface.items_tree["Factor"].identifier   # e.g. "Socket_2"

    # which frames to capture as morph poses (dense = every frame; sparse = hybrid)
    if not n_keys or n_keys >= n:
        key_frames = list(range(1, n + 1))
    else:
        key_frames = sorted({round(1 + k * (n - 1) / (n_keys - 1)) for k in range(n_keys)})

    # bake: at each pose frame set Factor, evaluate, capture verts → shape key whose
    # weight ramps 0→1→0 across its neighbours, so the runtime interpolates between
    # adjacent poses (continuous motion from a handful of keys).
    base_n = len(plant.data.vertices)
    plant.shape_key_add(name="Basis", from_mix=False)
    for idx, f in enumerate(key_frames):
        factor = (f - 1) / (n - 1)
        mod[fac_id] = factor
        plant.update_tag()
        bpy.context.scene.frame_set(f)
        deps = bpy.context.evaluated_depsgraph_get()
        ev = plant.evaluated_get(deps)
        coords = [v.co.copy() for v in ev.data.vertices]
        if len(coords) != base_n:
            raise RuntimeError(f"GN changed topology at frame {f} ({len(coords)} != {base_n})")
        sk = plant.shape_key_add(name=f"grow_{f:03d}", from_mix=False)
        for i, co in enumerate(coords):
            sk.data[i].co = co
        prev_f = key_frames[idx - 1] if idx > 0 else f
        next_f = key_frames[idx + 1] if idx < len(key_frames) - 1 else f
        sk.value = 0.0
        sk.keyframe_insert("value", frame=prev_f)
        sk.value = 1.0
        sk.keyframe_insert("value", frame=f)
        sk.value = 0.0
        sk.keyframe_insert("value", frame=next_f)
    # the GN modifier has done its job (baked into keys) → remove so export ships keys
    plant.modifiers.remove(mod)
    _set_interp(plant, "LINEAR")        # hat-function blend between sequential keys
    return [{"name": "grow", "role": "grow", "start": 1, "end": n, "fps": fps, "loop": False}]


# ── export (animation-preserving) ─────────────────────────────────────────────

def _export_glb_anim(path):
    try:
        bpy.ops.preferences.addon_enable(module="io_scene_gltf2")
    except Exception:
        pass
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", export_yup=True,
                              export_apply=False, export_animations=True,
                              export_morph=True, export_morph_animation=True)
    return Path(path).exists()


def _export_usdz_anim(path):
    rna = set(bpy.ops.wm.usd_export.get_rna_type().properties.keys())
    desired = {
        "filepath": str(path), "selected_objects_only": False, "export_uvmaps": True,
        "export_normals": True, "export_materials": True, "export_meshes": True,
        "generate_preview_surface": True, "convert_orientation": True,
        "export_global_forward_selection": "NEGATIVE_Z", "export_global_up_selection": "Y",
        "meters_per_unit": 1.0, "root_prim_path": "/Scene", "overwrite_textures": True,
        "export_animation": True, "export_shapekeys": True,
    }
    kwargs = {k: v for k, v in desired.items() if k in rna}
    kwargs["filepath"] = str(path)
    try:
        bpy.ops.wm.usd_export(**kwargs)
        return Path(path).exists()
    except Exception as e:  # noqa: BLE001
        print(f"[grow] usdz export failed: {e!r}")
        return False


# ── render a 3-frame filmstrip ────────────────────────────────────────────────

def _setup_render():
    scn = bpy.context.scene
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        try:
            scn.render.engine = eng
            break
        except TypeError:
            continue
    scn.render.resolution_x = 360
    scn.render.resolution_y = 540
    scn.render.film_transparent = False
    try:
        scn.view_settings.view_transform = "Standard"
    except Exception:
        pass
    w = bpy.data.worlds.new("w"); w.use_nodes = True
    bg = w.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.86, 0.87, 0.9, 1.0)
        bg.inputs[1].default_value = 0.9
    scn.world = w
    sd = bpy.data.lights.new("sun", type="SUN"); sd.energy = 3.2
    so = bpy.data.objects.new("sun", sd); bpy.context.collection.objects.link(so)
    so.rotation_euler = (math.radians(54), 0, math.radians(40))
    cam_d = bpy.data.cameras.new("cam"); cam_d.lens = 50
    cam = bpy.data.objects.new("cam", cam_d); bpy.context.collection.objects.link(cam)
    center = Vector((0, 0, POT_TOP_Z + STEM_H * 0.55))
    dist = 0.95
    cam.location = center + Vector((dist * 0.30, -dist, dist * 0.18))
    d = center - cam.location
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    scn.camera = cam


def _render_filmstrip(out_png, frames, label):
    from PIL import Image, ImageDraw
    scn = bpy.context.scene
    tiles = []
    for f in frames:
        scn.frame_set(f)
        p = str(Path(out_png).with_suffix(f".f{f}.png"))
        scn.render.filepath = p
        bpy.ops.render.render(write_still=True)
        tiles.append(Image.open(p).convert("RGB"))
    w = sum(t.width for t in tiles) + 8 * (len(tiles) - 1)
    h = max(t.height for t in tiles) + 26
    strip = Image.new("RGB", (w, h), (245, 245, 247))
    x = 0
    for t in tiles:
        strip.paste(t, (x, 26))
        x += t.width + 8
    d = ImageDraw.Draw(strip)
    d.text((4, 6), label, fill=(30, 30, 30))
    strip.save(out_png)
    return out_png


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    mode = (args[0] if args else "keyframed").strip().lower()
    out_dir = Path(args[1] if len(args) > 1 else r"C:\tmp\grow")
    out_dir.mkdir(parents=True, exist_ok=True)

    _clear()
    parts = build_plant()
    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = FRAMES

    if mode == "geonodes":
        clips = mode_geonodes(parts, FPS, FRAMES)
    elif mode == "hybrid":
        clips = mode_geonodes(parts, FPS, FRAMES, n_keys=8)   # author GN, ship 8 poses
    else:
        mode = "keyframed"
        clips = mode_keyframed(parts, FPS, FRAMES)

    _setup_render()
    strip = _render_filmstrip(out_dir / f"grow_{mode}.png",
                              [1, FRAMES // 2, FRAMES], f"{mode}  (sprout / mid / full)")

    glb = out_dir / f"grow_{mode}.glb"
    usdz = out_dir / f"grow_{mode}.usdz"
    ok_glb = _export_glb_anim(glb)
    ok_usdz = _export_usdz_anim(usdz)

    result = {
        "mode": mode, "clips": clips,
        "glb": str(glb) if ok_glb else "", "usdz": str(usdz) if ok_usdz else "",
        "glb_bytes": glb.stat().st_size if ok_glb else 0,
        "usdz_bytes": usdz.stat().st_size if ok_usdz else 0,
        "filmstrip": str(strip),
        "n_objects": len([o for o in bpy.data.objects if o.type == "MESH"]),
        "shape_keys": (len(bpy.data.objects["plant"].data.shape_keys.key_blocks)
                       if bpy.data.objects.get("plant")
                       and bpy.data.objects["plant"].data.shape_keys else 0),
    }
    (out_dir / f"grow_{mode}_result.json").write_text(json.dumps(result, indent=2),
                                                      encoding="utf-8")
    print("[grow] RESULT", json.dumps(result))


main()
