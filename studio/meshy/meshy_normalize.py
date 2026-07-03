"""meshy_normalize.py — run INSIDE Blender headless to bring a Meshy GLB into the
SPATAIL library at correct scale + RealityKit orientation.

    blender --background --python studio/meshy/meshy_normalize.py -- <spec.json>

spec: {result_path, assets:[{assetId, category, glb_in, scaleMeters, pivot, out_dir}]}

A Meshy model is arbitrary-scale and arbitrarily oriented. We import it, join the
meshes, WELD + SMOOTH (Meshy GLBs ship unwelded verts carrying per-face split
normals — the known faceted-seam bug), scale UNIFORMLY to the asset's real-world
scaleMeters (preserving Meshy's proportions + textures), seat it by pivot, and
re-export a metres / Y-up GLB + a RealityKit-ready USDZ — REUSING the proven
exporters in library/bake_assets.py, so a Meshy asset is shape/scale-identical in
pipeline terms to a procedurally baked one. Each result entry carries a QA report
{final_size_m, seated_z0, bbox_centered, split_normal_ratio} (ratio measured
BEFORE the weld so the metric shows what was fixed) + a cheap verification render
next to the export (<assetId>_qa.png).
"""
import bpy
import json
import math
import sys
from pathlib import Path

from mathutils import Vector

# reuse the proven fit + export helpers from the library bake
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "library"))
import bake_assets as BA  # noqa: E402

QA_RES = 512
SMOOTH_ANGLE_DEG = 35.0
WELD_REL = 1e-5                       # weld threshold as a share of the bbox diagonal


def _clear():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for blk in (bpy.data.meshes, bpy.data.materials, bpy.data.images,
                bpy.data.cameras, bpy.data.lights):
        for b in list(blk):
            if b.users == 0:
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


def _bbox(obj):
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


def _split_normal_ratio(me):
    """Share of interior edges (two adjacent faces) whose corner normals disagree
    across the edge — the unwelded-seam signature the weld+smooth repair removes.
    -1.0 = could not be measured (never fatal)."""
    try:
        loops = me.loops
        try:
            corner = me.corner_normals            # Blender 4.1+ / 5.x

            def _n(li):
                return corner[li].vector
        except AttributeError:                    # legacy loop normals
            try:
                me.calc_normals_split()
            except Exception:
                pass

            def _n(li):
                return loops[li].normal
        edge_polys = {}
        vert_loop = {}
        for poly in me.polygons:
            for li in poly.loop_indices:
                lp = loops[li]
                edge_polys.setdefault(lp.edge_index, []).append(poly.index)
                vert_loop[(poly.index, lp.vertex_index)] = li
        interior, split = 0, 0
        cos_eps = 0.99996                          # ~0.5 deg
        for ei, polys in edge_polys.items():
            if len(polys) != 2:
                continue
            interior += 1
            pa, pb = polys
            for v in me.edges[ei].vertices:
                la = vert_loop.get((pa, v))
                lb = vert_loop.get((pb, v))
                if la is None or lb is None:
                    continue
                na, nb = _n(la), _n(lb)
                if na.length > 1e-6 and nb.length > 1e-6 and \
                        na.normalized().dot(nb.normalized()) < cos_eps:
                    split += 1
                    break
        return round(split / interior, 4) if interior else 0.0
    except Exception as e:  # noqa: BLE001
        print(f"[normalize] split-normal scan failed: {e!r}")
        return -1.0


def _weld_smooth(obj, angle_deg=SMOOTH_ANGLE_DEG):
    """Kill the faceted-seam bug: merge vertices by a bbox-relative distance, drop
    the imported per-face custom split normals, then shade smooth keeping edges
    sharper than angle_deg. Never fatal (a failed weld ships the old behavior)."""
    try:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        mn, mx = _bbox(obj)
        thr = max((mx - mn).length * WELD_REL, 1e-7)
        before = len(obj.data.vertices)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.remove_doubles(threshold=thr)
        bpy.ops.object.mode_set(mode="OBJECT")
        try:
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
        except Exception:
            pass
        welded = before - len(obj.data.vertices)
        try:
            bpy.ops.object.shade_smooth_by_angle(angle=math.radians(angle_deg))
        except Exception:
            bpy.ops.object.shade_smooth()
        print(f"[normalize] weld+smooth: merged {welded} verts (thr={thr:.2e}), "
              f"smooth by angle {angle_deg:.0f} deg")
        return welded
    except Exception as e:  # noqa: BLE001
        print(f"[normalize] weld+smooth skipped: {e!r}")
        return 0


def _qa_report(obj, split_ratio):
    mn, mx = _bbox(obj)
    size = [round(mx[i] - mn[i], 6) for i in range(3)]
    longest = max(size) or 1.0
    return {
        "final_size_m": size,
        "seated_z0": bool(abs(mn.z) <= max(0.005, longest * 0.02)),
        "bbox_centered": bool(abs((mn.x + mx.x) / 2) <= 0.01
                              and abs((mn.y + mx.y) / 2) <= 0.01),
        "split_normal_ratio": split_ratio,
    }


def _qa_render(obj, out_path, res=QA_RES):
    """Cheap verification image next to the export — a small perspective render on
    a grid (the studio/vision/apply.py _verify_render approach, minus annotation).
    Runs AFTER export so render props never ship; failure never breaks normalize."""
    try:
        scn = bpy.context.scene
        for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
            try:
                scn.render.engine = eng
                break
            except TypeError:
                continue
        scn.render.resolution_x = scn.render.resolution_y = res
        scn.render.film_transparent = False
        scn.render.image_settings.file_format = "PNG"
        try:
            scn.view_settings.view_transform = "Standard"
        except Exception:
            pass
        world = bpy.data.worlds.new("qa_world")
        world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        if bg:
            bg.inputs[0].default_value = (0.6, 0.62, 0.66, 1)
            bg.inputs[1].default_value = 1.0
        scn.world = world
        sun = bpy.data.lights.new("qa_sun", type="SUN")
        sun.energy = 3.0
        so = bpy.data.objects.new("qa_sun", sun)
        bpy.context.collection.objects.link(so)
        so.rotation_euler = (math.radians(55), math.radians(10), math.radians(40))

        mn, mx = _bbox(obj)
        longest = max((mx[i] - mn[i]) for i in range(3)) or 0.1
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=12, y_subdivisions=12,
                                        size=longest * 3)
        grid = bpy.context.active_object
        grid.name = "qa_grid"
        wf = grid.modifiers.new("wire", "WIREFRAME")
        wf.thickness = longest * 0.006

        center = (mn + mx) / 2
        diag = (mx - mn).length or 0.1
        cam_d = bpy.data.cameras.new("qa_cam")
        cam_d.lens = 50
        cam_d.clip_start = 0.0001
        cam_d.clip_end = 1000
        cam = bpy.data.objects.new("qa_cam", cam_d)
        bpy.context.collection.objects.link(cam)
        cam.location = center + Vector((diag * 1.4, -diag * 1.7, diag * 1.2))
        cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
        scn.camera = cam
        scn.render.filepath = str(out_path)
        bpy.ops.render.render(write_still=True)
        print(f"[normalize] QA render: {out_path}")
        return str(out_path) if Path(out_path).exists() else ""
    except Exception as e:  # noqa: BLE001
        print(f"[normalize] QA render skipped: {e!r}")
        return ""


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
            split_ratio = _split_normal_ratio(obj.data)     # BEFORE the weld
            _weld_smooth(obj)
            BA._fit_uniform(obj, {"scaleMeters": a["scaleMeters"],
                                  "pivot": a.get("pivot", "center_bottom")})
            out = Path(a["out_dir"]); out.mkdir(parents=True, exist_ok=True)
            glb = out / f"{aid}.glb"; usdz = out / f"{aid}.usdz"
            ok_glb = BA._export_glb(glb)
            ok_usdz = BA._export_usdz(usdz)
            if not ok_glb:
                raise RuntimeError("GLB export failed")
            qa = _qa_report(obj, split_ratio)
            qa_png = _qa_render(obj, out / f"{aid}_qa.png")
            done.append({"assetId": aid, "category": a["category"], "glb": str(glb),
                         "usdz": str(usdz) if ok_usdz else "", "scaleMeters": a["scaleMeters"],
                         "qa": qa, "qa_render": qa_png})
            print(f"[normalize] {aid}: GLB{'+USDZ' if ok_usdz else ''} "
                  f"(split_normals {qa['split_normal_ratio']}, size {qa['final_size_m']})")
        except Exception as e:  # noqa: BLE001
            failed.append({"assetId": aid, "error": str(e)})
            print(f"[normalize] FAILED {aid}: {e}")
    Path(spec["result_path"]).write_text(
        json.dumps({"done": done, "failed": failed}, indent=2), encoding="utf-8")
    print(f"[normalize] {len(done)} ok, {len(failed)} failed")


main()
