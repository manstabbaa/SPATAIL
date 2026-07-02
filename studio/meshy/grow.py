"""grow.py — run INSIDE Blender headless: the HYBRID growth rig on a REAL asset.

    blender --background --python studio/meshy/grow.py -- <spec.json>

spec: {result_path, export_usdz?:bool, render?:bool,
       assets:[{assetId, glb_in, out_dir, n_keys?:int, fps?:int, seconds?:float}]}

Slice 2, production path (user's pick): AUTHOR the growth procedurally with Geometry
Nodes, then SHIP a sparse morph bake. Unlike the bake-off (grow_compare.py) which built
a primitive plant, this runs on an arbitrary imported mesh — e.g. a real Meshy plant —
because the growth keys off a per-vertex HEIGHT attribute, so it needs no part splitting.

How it grows (informed by the Blender manual — Set Position / shape-key rules):
  1. assign `growth_order` ∈ [0,1] per vertex = normalized height above the mesh base.
  2. a GN graph scales each point IN toward the mesh's bottom-centre by
     smoothstep(Factor − growth_order): low geometry emerges first, the plant rises.
     Pure Set Position → constant topology → shape-key bakeable ("do not add or remove
     vertices", manual/animation/shape_keys).
  3. sample the Factor sweep at n_keys poses, bake each as a shape key, ramp its weight
     0→1→0 across neighbours → the runtime interpolates a continuous grow from a handful
     of keys (lightweight morph targets, textures/UVs preserved).

Emits a "grow" clip (loop=False) the brief/animate path can call by name.
"""
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "library"))
import bake_assets as BA  # noqa: E402  (apply+join; we use animation-aware exporters below)

DEFAULT_KEYS = 8
DEFAULT_FPS = 30
DEFAULT_SECONDS = 4.0


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


def _import_glb(path):
    before = set(bpy.data.objects)
    try:
        bpy.ops.preferences.addon_enable(module="io_scene_gltf2")
    except Exception:
        pass
    bpy.ops.import_scene.gltf(filepath=str(path))
    return [o for o in bpy.data.objects if o not in before]


def _world_bbox(obj):
    lo = Vector((float("inf"),) * 3)
    hi = Vector((-float("inf"),) * 3)
    mw = obj.matrix_world
    for c in obj.bound_box:
        p = mw @ Vector(c)
        lo = Vector(map(min, lo, p))
        hi = Vector(map(max, hi, p))
    return lo, hi


# ── growth attribute + GN graph ───────────────────────────────────────────────

def _growth_order_attr(obj, z0, z1):
    """Per-vertex growth_order ∈ [0,1] = normalized height. Lower grows first."""
    me = obj.data
    span = max(z1 - z0, 1e-5)
    attr = me.attributes.get("growth_order") or me.attributes.new("growth_order", "FLOAT", "POINT")
    mw = obj.matrix_world
    for i, v in enumerate(me.vertices):
        wz = (mw @ v.co).z
        attr.data[i].value = min(max((wz - z0) / span, 0.0), 1.0)


def _build_grow_nodegroup(base_point, band=0.25, static_floor=0.0):
    """new_pos = base + (pos - base) * scale, where
        scale = max( smoothstep(Factor - growth_order),  growth_order < static_floor ).
    The smoothstep makes the plant rise from the base in height order; the static_floor
    term keeps low geometry (the pot/soil) always full-size so the plant grows OUT of a
    solid pot instead of the pot collapsing too. Pure Set Position → constant topology
    (shape-key bakeable)."""
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

    sub = nodes.new("ShaderNodeMath"); sub.operation = "SUBTRACT"
    links.new(gin.outputs["Factor"], sub.inputs[0])
    links.new(named.outputs["Attribute"], sub.inputs[1])

    mr = nodes.new("ShaderNodeMapRange"); mr.interpolation_type = "SMOOTHSTEP"
    mr.inputs["From Min"].default_value = 0.0
    mr.inputs["From Max"].default_value = band
    mr.inputs["To Min"].default_value = 0.0
    mr.inputs["To Max"].default_value = 1.0
    links.new(sub.outputs["Value"], mr.inputs["Value"])

    # static base: geometry below `static_floor` (the pot/soil) never collapses
    scale_src = mr.outputs["Result"]
    if static_floor > 0.0:
        lt = nodes.new("ShaderNodeMath"); lt.operation = "LESS_THAN"
        lt.inputs[1].default_value = static_floor
        links.new(named.outputs["Attribute"], lt.inputs[0])
        mx = nodes.new("ShaderNodeMath"); mx.operation = "MAXIMUM"
        links.new(mr.outputs["Result"], mx.inputs[0])
        links.new(lt.outputs["Value"], mx.inputs[1])
        scale_src = mx.outputs["Value"]

    base = nodes.new("FunctionNodeInputVector")
    base.vector = (base_point[0], base_point[1], base_point[2])
    pos = nodes.new("GeometryNodeInputPosition")
    delta = nodes.new("ShaderNodeVectorMath"); delta.operation = "SUBTRACT"
    links.new(pos.outputs["Position"], delta.inputs[0])
    links.new(base.outputs["Vector"], delta.inputs[1])
    scaled = nodes.new("ShaderNodeVectorMath"); scaled.operation = "SCALE"
    links.new(delta.outputs["Vector"], scaled.inputs[0])
    links.new(scale_src, scaled.inputs["Scale"])
    newpos = nodes.new("ShaderNodeVectorMath"); newpos.operation = "ADD"
    links.new(base.outputs["Vector"], newpos.inputs[0])
    links.new(scaled.outputs["Vector"], newpos.inputs[1])

    setpos = nodes.new("GeometryNodeSetPosition")
    links.new(gin.outputs["Geometry"], setpos.inputs["Geometry"])
    links.new(newpos.outputs["Vector"], setpos.inputs["Position"])
    links.new(setpos.outputs["Geometry"], gout.inputs["Geometry"])
    return ng


def _all_fcurves(obj):
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


def _set_interp(obj, interp="LINEAR"):
    for fc in _all_fcurves(obj):
        for kp in fc.keyframe_points:
            kp.interpolation = interp


# ── the grow rig ──────────────────────────────────────────────────────────────

def _grow_asset(a, export_usdz=False, render=False):
    glb_in = a["glb_in"]
    out_dir = Path(a["out_dir"]); out_dir.mkdir(parents=True, exist_ok=True)
    n_keys = int(a.get("n_keys", DEFAULT_KEYS))
    fps = int(a.get("fps", DEFAULT_FPS))
    n = max(2, int(round(float(a.get("seconds", DEFAULT_SECONDS)) * fps)))

    _clear()
    new = _import_glb(glb_in)
    meshes = [o for o in new if o.type == "MESH" and o.data and o.data.vertices]
    if not meshes:
        raise RuntimeError("no mesh in GLB")
    plant = BA._apply_and_join(meshes)            # one metric mesh, identity transform
    for o in list(bpy.data.objects):
        if o is not plant and o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    plant.name = "grow_subject"
    bpy.context.view_layer.update()

    lo, hi = _world_bbox(plant)
    base = ((lo.x + hi.x) / 2.0, (lo.y + hi.y) / 2.0, lo.z)   # bottom-centre = soil
    _growth_order_attr(plant, lo.z, hi.z)

    static_floor = float(a.get("static_floor", 0.0))          # keep the pot solid
    ng = _build_grow_nodegroup(base, static_floor=static_floor)
    mod = plant.modifiers.new("Grow", "NODES")
    mod.node_group = ng
    fac_id = ng.interface.items_tree["Factor"].identifier

    key_frames = sorted({round(1 + k * (n - 1) / (n_keys - 1)) for k in range(n_keys)}) \
        if n_keys < n else list(range(1, n + 1))

    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = n
    base_n = len(plant.data.vertices)
    plant.shape_key_add(name="Basis", from_mix=False)
    for idx, f in enumerate(key_frames):
        mod[fac_id] = (f - 1) / (n - 1)
        plant.update_tag()
        bpy.context.scene.frame_set(f)
        deps = bpy.context.evaluated_depsgraph_get()
        ev = plant.evaluated_get(deps)
        coords = [v.co.copy() for v in ev.data.vertices]
        if len(coords) != base_n:
            raise RuntimeError(f"GN changed topology at frame {f}")
        sk = plant.shape_key_add(name=f"grow_{f:03d}", from_mix=False)
        for i, co in enumerate(coords):
            sk.data[i].co = co
        prev_f = key_frames[idx - 1] if idx > 0 else f
        next_f = key_frames[idx + 1] if idx < len(key_frames) - 1 else f
        sk.value = 0.0; sk.keyframe_insert("value", frame=prev_f)
        sk.value = 1.0; sk.keyframe_insert("value", frame=f)
        sk.value = 0.0; sk.keyframe_insert("value", frame=next_f)
    plant.modifiers.remove(mod)
    _set_interp(plant, "LINEAR")

    glb = out_dir / f"{a['assetId']}_grow.glb"
    usdz = out_dir / f"{a['assetId']}_grow.usdz"
    ok_glb = _export_glb_anim(glb)
    ok_usdz = _export_usdz_anim(usdz) if export_usdz else False

    strip = ""
    if render:
        strip = str(_render_filmstrip(plant, lo, hi, out_dir / f"{a['assetId']}_grow.png",
                                      [1, n // 2, n]))

    clips = [{"name": "grow", "role": "grow", "start": 1, "end": n, "fps": fps, "loop": False}]
    print(f"[grow] {a['assetId']}: {len(key_frames)} poses, {base_n} verts, "
          f"GLB {'ok' if ok_glb else 'FAIL'} ({glb.stat().st_size if ok_glb else 0} B)")
    return {"assetId": a["assetId"], "clips": clips,
            "glb": str(glb) if ok_glb else "", "usdz": str(usdz) if ok_usdz else "",
            "glb_bytes": glb.stat().st_size if ok_glb else 0,
            "usdz_bytes": usdz.stat().st_size if ok_usdz else 0,
            "n_keys": len(key_frames), "verts": base_n, "filmstrip": strip}


# ── export (animation + morph) ────────────────────────────────────────────────

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


# ── filmstrip ─────────────────────────────────────────────────────────────────

def _render_filmstrip(obj, lo, hi, out_png, frames):
    from PIL import Image, ImageDraw
    scn = bpy.context.scene
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        try:
            scn.render.engine = eng
            break
        except TypeError:
            continue
    scn.render.resolution_x = 360
    scn.render.resolution_y = 540
    try:
        scn.view_settings.view_transform = "Standard"
    except Exception:
        pass
    w = bpy.data.worlds.new("w"); w.use_nodes = True
    bg = w.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.86, 0.87, 0.9, 1.0)
        bg.inputs[1].default_value = 0.95
    scn.world = w
    sd = bpy.data.lights.new("sun", type="SUN"); sd.energy = 3.2
    so = bpy.data.objects.new("sun", sd); bpy.context.collection.objects.link(so)
    so.rotation_euler = (math.radians(54), 0, math.radians(40))
    center = (lo + hi) * 0.5
    diag = max((hi - lo).length, 0.05)
    cam_d = bpy.data.cameras.new("cam"); cam_d.lens = 52
    cam = bpy.data.objects.new("cam", cam_d); bpy.context.collection.objects.link(cam)
    cam.location = center + Vector((diag * 0.35, -diag * 1.05, diag * 0.18))
    d = center - cam.location
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    scn.camera = cam

    tiles = []
    for f in frames:
        scn.frame_set(f)
        p = str(Path(out_png).with_suffix(f".f{f}.png"))
        scn.render.filepath = p
        bpy.ops.render.render(write_still=True)
        tiles.append(Image.open(p).convert("RGB"))
    gap = 8
    cw = sum(t.width for t in tiles) + gap * (len(tiles) - 1)
    ch = max(t.height for t in tiles) + 26
    strip = Image.new("RGB", (cw, ch), (245, 245, 247))
    x = 0
    for t in tiles:
        strip.paste(t, (x, 26))
        x += t.width + gap
    ImageDraw.Draw(strip).text((4, 6), "meshy grow  (sprout / mid / full)", fill=(30, 30, 30))
    strip.save(out_png)
    return out_png


def main():
    spec = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    export_usdz = bool(spec.get("export_usdz", False))
    render = bool(spec.get("render", False))
    done, failed = [], []
    for a in spec["assets"]:
        try:
            done.append(_grow_asset(a, export_usdz=export_usdz, render=render))
        except Exception as e:  # noqa: BLE001
            failed.append({"assetId": a.get("assetId"), "error": str(e)[:400]})
            print(f"[grow] FAILED {a.get('assetId')}: {e}")
    Path(spec["result_path"]).write_text(
        json.dumps({"done": done, "failed": failed}, indent=2), encoding="utf-8")
    print(f"[grow] {len(done)} ok, {len(failed)} failed")


main()
