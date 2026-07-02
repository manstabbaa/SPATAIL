"""decimate.py — run INSIDE Blender headless: shrink a Meshy GLB for mobile AR.

    blender --background --python studio/meshy/decimate.py -- <spec.json>

spec: {result_path, target_tris, texture_max, assets:[{assetId, glb_in, out_dir}]}

A Meshy model is ~70K tris with 2K PBR textures -> 12-22 MB (texture-dominated). For
AR Quick Look / RealityKit we (a) DECIMATE geometry to target_tris and (b) DOWNSCALE
every texture to texture_max px, then re-export a Draco GLB + a RealityKit USDZ named
<id>_ar.{glb,usdz}. Reuses library/bake_assets exporters; reports before/after.
"""
import bpy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "library"))
import bake_assets as BA  # noqa: E402


def _clear():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for blk in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for b in list(blk):
            if b.users == 0:
                try: blk.remove(b)
                except Exception: pass


def _import(path):
    before = set(bpy.data.objects)
    try: bpy.ops.preferences.addon_enable(module="io_scene_gltf2")
    except Exception: pass
    bpy.ops.import_scene.gltf(filepath=str(path))
    return [o for o in bpy.data.objects if o not in before]


def _tris(obj):
    me = obj.data
    me.calc_loop_triangles()
    return len(me.loop_triangles)


def _glb_draco(path):
    try:
        bpy.ops.export_scene.gltf(
            filepath=str(path), export_format="GLB", export_yup=True, export_apply=True,
            export_draco_mesh_compression_enable=True, export_draco_mesh_compression_level=6)
        return path.exists()
    except TypeError:
        bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB",
                                  export_yup=True, export_apply=True)
        return path.exists()


def main():
    spec = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    tgt = int(spec.get("target_tris", 12000))
    tmax = int(spec.get("texture_max", 1024))
    done = []
    for a in spec["assets"]:
        _clear()
        meshes = [o for o in _import(a["glb_in"]) if o.type == "MESH"]
        obj = BA._apply_and_join(meshes)
        for o in list(bpy.data.objects):
            if o is not obj:
                bpy.data.objects.remove(o, do_unlink=True)
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True); bpy.context.view_layer.objects.active = obj
        t0 = _tris(obj)
        if t0 > tgt:
            m = obj.modifiers.new("dec", "DECIMATE")
            m.decimate_type = "COLLAPSE"; m.ratio = max(0.02, tgt / t0)
            bpy.ops.object.modifier_apply(modifier=m.name)
        t1 = _tris(obj)
        tex = []
        for img in list(bpy.data.images):
            if img.type in {"RENDER_RESULT", "COMPOSITING"} or not img.has_data:
                continue
            w, h = img.size
            if w > 0 and h > 0 and max(w, h) > tmax:
                s = tmax / max(w, h); nw, nh = max(1, int(w * s)), max(1, int(h * s))
                img.scale(nw, nh)
                try: img.pack()
                except Exception: pass
                tex.append(f"{w}x{h}->{nw}x{nh}")
        out_dir = Path(a["out_dir"]); out_dir.mkdir(parents=True, exist_ok=True)
        glb = out_dir / f"{a['assetId']}_ar.glb"; usdz = out_dir / f"{a['assetId']}_ar.usdz"
        ok_glb = _glb_draco(glb); ok_usdz = BA._export_usdz(usdz)
        done.append({"assetId": a["assetId"], "tris_before": t0, "tris_after": t1,
                     "textures": tex, "glb": str(glb) if ok_glb else "",
                     "usdz": str(usdz) if ok_usdz else ""})
        print(f"[decimate] {a['assetId']}: {t0}->{t1} tris; tex {tex}")
    Path(spec["result_path"]).write_text(json.dumps({"done": done}, indent=2), encoding="utf-8")
    print(f"[decimate] {len(done)} assets")


main()
