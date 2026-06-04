"""meshy_normalize.py — run INSIDE Blender headless to bring a Meshy GLB into the
SPATAIL library at correct scale + RealityKit orientation.

    blender --background --python studio/meshy/meshy_normalize.py -- <spec.json>

spec: {result_path, assets:[{assetId, category, glb_in, scaleMeters, pivot, out_dir}]}

A Meshy model is arbitrary-scale and arbitrarily oriented. We import it, join the
meshes, scale UNIFORMLY to the asset's real-world scaleMeters (preserving Meshy's
proportions + textures), seat it by pivot, and re-export a metres / Y-up GLB + a
RealityKit-ready USDZ — REUSING the proven exporters in library/bake_assets.py, so a
Meshy asset is shape/scale-identical in pipeline terms to a procedurally baked one.
"""
import bpy
import json
import sys
from pathlib import Path

# reuse the proven fit + export helpers from the library bake
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
                blk.remove(b)


def _import_glb(path):
    before = set(bpy.data.objects)
    try:
        bpy.ops.preferences.addon_enable(module="io_scene_gltf2")
    except Exception:
        pass
    bpy.ops.import_scene.gltf(filepath=str(path))
    return [o for o in bpy.data.objects if o not in before]


def main():
    spec = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    done, failed = [], []
    for a in spec["assets"]:
        aid = a["assetId"]
        try:
            _clear()
            new = _import_glb(a["glb_in"])
            meshes = [o for o in new if o.type == "MESH"]
            if not meshes:
                raise RuntimeError("no mesh in Meshy GLB")
            obj = BA._apply_and_join(meshes)
            # drop any leftover empties/lights from the import so export is clean
            for o in list(bpy.data.objects):
                if o is not obj:
                    bpy.data.objects.remove(o, do_unlink=True)
            BA._fit_uniform(obj, {"scaleMeters": a["scaleMeters"],
                                  "pivot": a.get("pivot", "center_bottom")})
            out = Path(a["out_dir"]); out.mkdir(parents=True, exist_ok=True)
            glb = out / f"{aid}.glb"; usdz = out / f"{aid}.usdz"
            ok_glb = BA._export_glb(glb)
            ok_usdz = BA._export_usdz(usdz)
            if not ok_glb:
                raise RuntimeError("GLB export failed")
            done.append({"assetId": aid, "category": a["category"], "glb": str(glb),
                         "usdz": str(usdz) if ok_usdz else "", "scaleMeters": a["scaleMeters"]})
            print(f"[normalize] {aid}: GLB{'+USDZ' if ok_usdz else ''}")
        except Exception as e:  # noqa: BLE001
            failed.append({"assetId": aid, "error": str(e)})
            print(f"[normalize] FAILED {aid}: {e}")
    Path(spec["result_path"]).write_text(
        json.dumps({"done": done, "failed": failed}, indent=2), encoding="utf-8")
    print(f"[normalize] {len(done)} ok, {len(failed)} failed")


main()
