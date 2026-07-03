"""animate.py — run INSIDE Blender headless: give a Meshy asset its MOTION.

    blender --background --python studio/meshy/animate.py -- <spec.json>

spec: {result_path, assets:[{assetId, glb_in, out_dir, motion, fps, seconds}]}
motion: {"kind": "showcase"|"turntable"|"swing"|"bob", params...}

The "filler rig" stage of the studio pipeline: Meshy models the asset (static),
THIS bakes object-level keyframed clips onto it — a seamless loop the phone's
clip player (and the web viewer) plays — and re-exports <id>_anim.glb/.usdz WITH
animation. Whole-object motion only (the mesh is joined); per-part rigging on
split meshes is the next stage. Loops are sampled per-frame with linear
interpolation so the cycle is exact.
"""
import bpy
import json
import math
import sys
from pathlib import Path

from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "library"))
import bake_assets as BA  # noqa: E402  (reuse the proven GLB exporter)

SMOOTH_ANGLE_DEG = 35.0
WELD_REL = 1e-5                       # weld threshold as a share of the bbox diagonal


def _clear():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for blk in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.actions):
        for b in list(blk):
            if b.users == 0:
                try:
                    blk.remove(b)
                except Exception:  # noqa: BLE001
                    pass


def _import_glb(path):
    before = set(bpy.data.objects)
    try:
        bpy.ops.preferences.addon_enable(module="io_scene_gltf2")
    except Exception:  # noqa: BLE001
        pass
    bpy.ops.import_scene.gltf(filepath=str(path))
    return [o for o in bpy.data.objects if o not in before]


def _weld_smooth(obj, angle_deg=SMOOTH_ANGLE_DEG):
    """Same faceted-seam fix as meshy_normalize: weld by a bbox-relative distance,
    drop imported per-face custom split normals, shade smooth by angle. Applied
    where the geometry is FINAL (right after import, before parenting/baking —
    motion here is object-level keyframes, never mesh edits). Never fatal."""
    try:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.context.view_layer.update()
        pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
        mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
        thr = max((mx - mn).length * WELD_REL, 1e-7)
        before = len(obj.data.vertices)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.remove_doubles(threshold=thr)
        bpy.ops.object.mode_set(mode="OBJECT")
        try:
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
        except Exception:  # noqa: BLE001
            pass
        welded = before - len(obj.data.vertices)
        try:
            bpy.ops.object.shade_smooth_by_angle(angle=math.radians(angle_deg))
        except Exception:  # noqa: BLE001
            bpy.ops.object.shade_smooth()
        print(f"[animate] weld+smooth {obj.name}: merged {welded} verts "
              f"(thr={thr:.2e}), smooth by angle {angle_deg:.0f} deg")
        return welded
    except Exception as e:  # noqa: BLE001
        print(f"[animate] weld+smooth skipped for {obj.name}: {e!r}")
        return 0


def _root_over(meshes, name="anim_root", pivot_z=0.0):
    """Parent every imported mesh under one empty so ONE transform drives the
    whole asset; the empty's origin is the motion pivot."""
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    root.location = (0.0, 0.0, pivot_z)
    for o in meshes:
        o.select_set(False)
        keep = o.matrix_world.copy()
        o.parent = root
        o.matrix_world = keep
    return root


def _set_linear_default():
    """New keyframes interpolate LINEAR (Blender 5.x dropped Action.fcurves, so
    we set the insertion default instead of editing curves after the fact).
    With per-frame sampling the interpolation mode barely matters anyway."""
    try:
        bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
    except Exception:  # noqa: BLE001
        pass


def _count_fcurves() -> int:
    """Diagnostic only — tolerant across the legacy (<=4.x) and the layered
    (5.x: action.layers[].strips[].channelbags[].fcurves) action data models."""
    n = 0
    for o in bpy.data.objects:
        act = o.animation_data.action if o.animation_data else None
        if not act:
            continue
        try:
            n += len(act.fcurves)                      # legacy API
        except AttributeError:
            try:
                for layer in act.layers:
                    for strip in layer.strips:
                        for bag in strip.channelbags:
                            n += len(bag.fcurves)
            except Exception:  # noqa: BLE001
                pass
    return n


def _linear(obj, path, index, samples):
    """Per-frame keyframes — exact seamless loops regardless of interpolation."""
    for f, v in samples:
        if index < 0:
            setattr(obj, path, v)
        else:
            vec = getattr(obj, path)
            vec[index] = v
            setattr(obj, path, vec)
        obj.keyframe_insert(data_path=path, index=index, frame=f)


def _height(meshes) -> float:
    top = 0.0
    for o in meshes:
        for c in o.bound_box:
            w = o.matrix_world @ __import__("mathutils").Vector(c)
            top = max(top, w.z)
    return top


def _bake(root, meshes, motion, fps, seconds):
    """Returns the clip list this motion produced."""
    kind = (motion or {}).get("kind", "showcase")
    n = max(int(fps * seconds), fps)
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, n
    sc.render.fps = fps

    if kind == "swing":
        # pendulum: pivot at the TOP of the asset, sinusoidal rotation about X
        root.location = (0.0, 0.0, _height(meshes))
        for o in meshes:
            o.matrix_parent_inverse = root.matrix_world.inverted()
        amp = math.radians(float((motion or {}).get("amplitude_deg", 25.0)))
        _linear(root, "rotation_euler", 0,
                [(f, amp * math.sin(2 * math.pi * (f - 1) / (n - 1))) for f in range(1, n + 1)])
        return [{"name": "swing", "role": "demo", "start": 1, "end": n, "fps": fps, "loop": True}]

    if kind in ("turntable", "spin"):
        _linear(root, "rotation_euler", 2,
                [(f, 2 * math.pi * (f - 1) / (n - 1)) for f in range(1, n + 1)])
        return [{"name": "turntable", "role": "demo", "start": 1, "end": n, "fps": fps, "loop": True}]

    if kind == "bob":
        amp = float((motion or {}).get("amplitude_m", 0.02))
        _linear(root, "location", 2,
                [(f, amp * (1 - math.cos(2 * math.pi * (f - 1) / (n - 1))) / 2) for f in range(1, n + 1)])
        return [{"name": "bob", "role": "demo", "start": 1, "end": n, "fps": fps, "loop": True}]

    # default "showcase": slow turntable + a subtle breathing bob — inspectable
    # from every side, obviously alive, safe for ANY object category.
    _linear(root, "rotation_euler", 2,
            [(f, 2 * math.pi * (f - 1) / (n - 1)) for f in range(1, n + 1)])
    amp = float((motion or {}).get("amplitude_m", 0.012))
    _linear(root, "location", 2,
            [(f, amp * (1 - math.cos(4 * math.pi * (f - 1) / (n - 1))) / 2) for f in range(1, n + 1)])
    return [{"name": "showcase", "role": "demo", "start": 1, "end": n, "fps": fps, "loop": True}]


def _export_usdz_animated(path: Path) -> bool:
    rna = set(bpy.ops.wm.usd_export.get_rna_type().properties.keys())
    desired = {
        "filepath": str(path), "selected_objects_only": False, "export_uvmaps": True,
        "export_normals": True, "export_materials": True, "export_meshes": True,
        "generate_preview_surface": True, "convert_orientation": True,
        "export_global_forward_selection": "NEGATIVE_Z", "export_global_up_selection": "Y",
        "meters_per_unit": 1.0, "root_prim_path": "/Scene", "overwrite_textures": True,
        "export_animation": True,            # ← the whole point of this exporter
    }
    kwargs = {k: v for k, v in desired.items() if k in rna}
    kwargs["filepath"] = str(path)
    try:
        bpy.ops.wm.usd_export(**kwargs)
        return path.exists()
    except Exception as e:  # noqa: BLE001
        print(f"[animate] USDZ export failed for {path.name}: {e!r}")
        return False


def main():
    spec = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    _set_linear_default()
    done, failed = [], []
    for a in spec["assets"]:
        aid = a["assetId"]
        try:
            _clear()
            meshes = [o for o in _import_glb(a["glb_in"]) if o.type == "MESH"]
            if not meshes:
                raise RuntimeError("no mesh to animate")
            for m in meshes:
                _weld_smooth(m)
            root = _root_over(meshes)
            clips = _bake(root, meshes, a.get("motion"), int(a.get("fps", 30)),
                          float(a.get("seconds", 6.0)))
            out = Path(a["out_dir"])
            out.mkdir(parents=True, exist_ok=True)
            glb = out / f"{aid}_anim.glb"
            usdz = out / f"{aid}_anim.usdz"
            ok_glb = BA._export_glb(glb)              # gltf exports animations by default
            ok_usdz = _export_usdz_animated(usdz)
            if not ok_glb:
                raise RuntimeError("animated GLB export failed")
            fcurves = _count_fcurves()
            done.append({"assetId": aid, "clips": clips, "glb": str(glb),
                         "usdz": str(usdz) if ok_usdz else "", "fcurves": fcurves})
            print(f"[animate] {aid}: {clips[0]['name']} f1-{clips[0]['end']} "
                  f"({fcurves} fcurves) GLB{'+USDZ' if ok_usdz else ''}")
        except Exception as e:  # noqa: BLE001
            failed.append({"assetId": aid, "error": str(e)})
            print(f"[animate] FAILED {aid}: {e}")
    Path(spec["result_path"]).write_text(
        json.dumps({"done": done, "failed": failed}, indent=2), encoding="utf-8")
    print(f"[animate] {len(done)} ok, {len(failed)} failed")


main()
