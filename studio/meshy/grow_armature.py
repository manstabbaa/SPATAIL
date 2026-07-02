"""grow_armature.py — run INSIDE Blender headless: the WEIGHT-PAINTED ARMATURE grow rig.

    blender --background --python studio/meshy/grow_armature.py -- <spec.json>

spec: {result_path, export_usdz?, render?,
       assets:[{assetId, glb_in (.fbx/.glb/.obj), out_dir,
                n_bones?, fps?, seconds?, static_floor?}]}

The rig direction the user asked for: DON'T cut/segment the mesh. Take ONE clean
(quad-remeshed) mesh, build an armature bone-chain up its main axis, bind the mesh with
AUTOMATIC WEIGHTS (bone-heat weight painting — no manual cutting), then animate the pose
bones to grow base→tip. The single connected surface deforms as one skin → no gaps. It
exports as true skeletal animation (glTF skins / UsdSkel), which RealityKit plays.

`static_floor` keeps the lowest bones (the pot/soil) at full scale so the plant grows OUT
of a solid pot. Recipe validated headless on Blender 5.1.1.
"""
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "library"))
import bake_assets as BA  # noqa: E402


def _clear():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for blk in (bpy.data.meshes, bpy.data.materials, bpy.data.images,
                bpy.data.armatures, bpy.data.objects):
        for b in list(blk):
            if getattr(b, "users", 0) == 0:
                try:
                    blk.remove(b)
                except Exception:
                    pass


def _import_mesh(path):
    """Import GLB/GLTF/FBX/OBJ. FBX/OBJ preserve QUAD topology (glTF triangulates)."""
    before = set(bpy.data.objects)
    ext = Path(path).suffix.lower()
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    else:
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


def _set_interp(obj, interp="BEZIER"):
    for fc in _all_fcurves(obj):
        for kp in fc.keyframe_points:
            kp.interpolation = interp


# ── the armature grow rig ─────────────────────────────────────────────────────

def _grow_asset(a, export_usdz=False, render=False):
    glb_in = a["glb_in"]
    out_dir = Path(a["out_dir"]); out_dir.mkdir(parents=True, exist_ok=True)
    n_bones = int(a.get("n_bones", 6))
    fps = int(a.get("fps", 30))
    end = max(2, int(round(float(a.get("seconds", 4.0)) * fps)))
    static_floor = float(a.get("static_floor", 0.0))

    _clear()
    new = _import_mesh(glb_in)
    meshes = [o for o in new if o.type == "MESH" and o.data and o.data.vertices]
    if not meshes:
        raise RuntimeError("no mesh in input")
    mesh = BA._apply_and_join(meshes)
    for o in list(bpy.data.objects):
        if o is not mesh and o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    mesh.name = f"{a['assetId']}_grow"
    vl = bpy.context.view_layer
    vl.update()

    lo, hi = _world_bbox(mesh)
    z0, z1 = lo.z, hi.z
    seg = max((z1 - z0) / n_bones, 1e-4)
    cx, cy = (lo.x + hi.x) / 2.0, (lo.y + hi.y) / 2.0   # bone chain on the central axis

    # 1) armature + bone chain up +Z (edit mode)
    adat = bpy.data.armatures.new(f"{a['assetId']}_arm")
    arm = bpy.data.objects.new(f"{a['assetId']}_arm", adat)
    bpy.context.collection.objects.link(arm)
    vl.objects.active = arm
    names = []
    with bpy.context.temp_override(active_object=arm, object=arm,
                                   selected_objects=[arm], selected_editable_objects=[arm]):
        bpy.ops.object.mode_set(mode="EDIT")
        prev = None
        for i in range(n_bones):
            b = adat.edit_bones.new(f"grow_{i}")
            b.head = (cx, cy, z0 + i * seg)
            b.tail = (cx, cy, z0 + (i + 1) * seg)
            b.use_deform = True
            if prev:
                b.parent = prev
                b.use_connect = True
            prev = b
            names.append(b.name)
        bpy.ops.object.mode_set(mode="OBJECT")

    # 2) bind mesh → armature WITH AUTOMATIC (bone-heat) WEIGHTS — no cutting
    for o in vl.objects:
        o.select_set(False)
    mesh.select_set(True); arm.select_set(True); vl.objects.active = arm
    with bpy.context.temp_override(active_object=arm, object=arm,
                                   selected_editable_objects=[mesh, arm],
                                   selected_objects=[mesh, arm]):
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    if not any(m.type == "ARMATURE" for m in mesh.modifiers):
        raise RuntimeError("auto-weight bind failed (no armature modifier)")
    n_vgroups = len(mesh.vertex_groups)

    # 3) pose-bone keyframes: grow base→tip; static_floor bones (the pot) stay full
    scn = bpy.context.scene
    scn.frame_start = 1
    scn.frame_end = end
    floor_z = z0 + static_floor * (z1 - z0)
    grow_idx = [i for i in range(n_bones) if (z0 + (i + 0.5) * seg) >= floor_z]
    span = max(len(grow_idx), 1)
    vl.objects.active = arm
    with bpy.context.temp_override(active_object=arm, object=arm,
                                   selected_objects=[arm], selected_editable_objects=[arm]):
        bpy.ops.object.mode_set(mode="POSE")
        # static (pot) bones: pinned full the whole clip
        for i in range(n_bones):
            if i in grow_idx:
                continue
            pb = arm.pose.bones[names[i]]
            pb.scale = (1.0, 1.0, 1.0)
            pb.keyframe_insert("scale", frame=1)
            pb.keyframe_insert("scale", frame=end)
        # growing bones: each ramps 0→full, staggered low→high (the plant rises)
        for k, i in enumerate(grow_idx):
            pb = arm.pose.bones[names[i]]
            s = 1 + int((end - 1) * (k / (span + 1)))
            e = min(end, s + max(2, int((end - 1) * 1.4 / (span + 1))))
            pb.scale = (0.02, 0.02, 0.02); pb.keyframe_insert("scale", frame=1)
            pb.scale = (0.02, 0.02, 0.02); pb.keyframe_insert("scale", frame=s)
            pb.scale = (1.0, 1.0, 1.0); pb.keyframe_insert("scale", frame=e)
            pb.scale = (1.0, 1.0, 1.0); pb.keyframe_insert("scale", frame=end)
        bpy.ops.object.mode_set(mode="OBJECT")
    _set_interp(arm, "BEZIER")

    # 4) export skeletal animation (glTF skins + UsdSkel)
    glb = out_dir / f"{a['assetId']}_grow.glb"
    usdz = out_dir / f"{a['assetId']}_grow.usdz"
    ok_glb = _export_glb_skel(glb)
    ok_usdz = _export_usdz_skel(usdz) if export_usdz else False

    strip = ""
    if render:
        strip = str(_render_filmstrip(lo, hi, out_dir / f"{a['assetId']}_grow.png",
                                      [1, end // 2, end]))

    clips = [{"name": "grow", "role": "grow", "start": 1, "end": end, "fps": fps, "loop": False}]
    print(f"[grow_arm] {a['assetId']}: {n_bones} bones, {n_vgroups} vgroups, "
          f"GLB {'ok' if ok_glb else 'FAIL'} ({glb.stat().st_size if ok_glb else 0} B)")
    return {"assetId": a["assetId"], "clips": clips,
            "glb": str(glb) if ok_glb else "", "usdz": str(usdz) if ok_usdz else "",
            "glb_bytes": glb.stat().st_size if ok_glb else 0,
            "usdz_bytes": usdz.stat().st_size if ok_usdz else 0,
            "bones": n_bones, "vgroups": n_vgroups, "verts": len(mesh.data.vertices),
            "filmstrip": strip, "rig": "armature"}


def _export_glb_skel(path):
    try:
        bpy.ops.preferences.addon_enable(module="io_scene_gltf2")
    except Exception:
        pass
    for o in bpy.context.view_layer.objects:
        o.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", export_yup=True,
                              export_apply=False, export_animations=True, export_skins=True)
    return Path(path).exists()


def _export_usdz_skel(path):
    rna = set(bpy.ops.wm.usd_export.get_rna_type().properties.keys())
    desired = {
        "filepath": str(path), "selected_objects_only": False, "export_uvmaps": True,
        "export_normals": True, "export_materials": True, "export_meshes": True,
        "generate_preview_surface": True, "convert_orientation": True,
        "export_global_forward_selection": "NEGATIVE_Z", "export_global_up_selection": "Y",
        "meters_per_unit": 1.0, "root_prim_path": "/Scene", "overwrite_textures": True,
        "export_animation": True, "export_armatures": True, "only_deform_bones": False,
    }
    kwargs = {k: v for k, v in desired.items() if k in rna}
    kwargs["filepath"] = str(path)
    try:
        bpy.ops.wm.usd_export(**kwargs)
        return Path(path).exists()
    except Exception as e:  # noqa: BLE001
        print(f"[grow_arm] usdz export failed: {e!r}")
        return False


def _render_filmstrip(lo, hi, out_png, frames):
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
        strip.paste(t, (x, 26)); x += t.width + gap
    ImageDraw.Draw(strip).text((4, 6), "armature grow (sprout / mid / full)", fill=(30, 30, 30))
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
            print(f"[grow_arm] FAILED {a.get('assetId')}: {e}")
    Path(spec["result_path"]).write_text(
        json.dumps({"done": done, "failed": failed}, indent=2), encoding="utf-8")
    print(f"[grow_arm] {len(done)} ok, {len(failed)} failed")


main()
