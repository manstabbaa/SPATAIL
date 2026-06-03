"""bake_assets.py — headless Blender: bake library assets to real GLB + USDZ.

    blender --background --python studio/library/bake_assets.py -- <spec.json>

Spec JSON: {repoRoot, result_path, assets: [{assetId, category, fallbackPrimitive,
scaleMeters, pivot}]}. For each asset it builds clean geometry (the fallback
primitive, or a small composed recipe for hero shapes), sizes it to scaleMeters,
seats it by pivot, gives it a category-coloured Principled material, and exports a
metres / Y-up GLB + a RealityKit-ready USDZ into
public/assets/spatail-library/<category>/<id>.{glb,usdz}. Resilient: one bad asset
is recorded and skipped, the rest continue. Writes a result sidecar the orchestrator
(bake_run.py) reads to register the baked assets into the library.

Export settings mirror studio/blender/build_studio.py (proven on Blender 5.1 / iOS).
"""
import bpy
import json
import sys
import math
from pathlib import Path

from mathutils import Vector

# ── colours (Principled base color rgba) ─────────────────────────────────────
_CAT_COLOR = {
    "mechanical": (0.55, 0.57, 0.60, 1), "biology": (0.85, 0.30, 0.34, 1),
    "physics": (0.20, 0.62, 0.66, 1), "astronomy": (0.65, 0.66, 0.72, 1),
    "architecture": (0.72, 0.70, 0.64, 1), "furniture": (0.55, 0.42, 0.30, 1),
    "product-preview": (0.18, 0.19, 0.22, 1), "data-visualization": (0.31, 0.27, 0.90, 1),
    "primitives": (0.70, 0.72, 0.75, 1), "spatial-ui": (0.31, 0.27, 0.90, 1),
    "labels-and-callouts": (0.31, 0.27, 0.90, 1), "environments": (0.5, 0.5, 0.5, 1),
    "education": (0.40, 0.55, 0.85, 1), "effects": (0.9, 0.7, 0.2, 1),
    "placeholders": (0.45, 0.47, 0.50, 1), "animation-rigs": (0.5, 0.5, 0.5, 1),
}
_ID_COLOR = {
    "sun": (1.0, 0.78, 0.18, 1), "earth": (0.20, 0.45, 0.80, 1), "moon": (0.72, 0.72, 0.74, 1),
    "mars": (0.78, 0.34, 0.22, 1), "jupiter": (0.80, 0.66, 0.50, 1), "saturn": (0.86, 0.78, 0.55, 1),
    "venus": (0.88, 0.78, 0.52, 1), "mercury": (0.6, 0.58, 0.55, 1), "apple": (0.82, 0.18, 0.18, 1),
    "generic_cell": (0.78, 0.55, 0.80, 1), "nucleus": (0.45, 0.25, 0.55, 1),
    "heart_simplified": (0.78, 0.16, 0.20, 1), "neuron": (0.85, 0.72, 0.45, 1),
    "dna_double_helix": (0.25, 0.65, 0.85, 1), "atom_nucleus": (0.85, 0.35, 0.30, 1),
}
_EMISSIVE = {"sun", "glow_ring", "pulse_glow", "energy_beam", "hologram_material"}


def _clear():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for block in (bpy.data.meshes, bpy.data.materials):
        for b in list(block):
            if b.users == 0:
                block.remove(b)


def _prim(kind: str):
    k = (kind or "cube").lower()
    if k in ("sphere", "half_sphere"):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, segments=32, ring_count=16)
    elif k == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=1.0, vertices=32)
    elif k == "cone":
        bpy.ops.mesh.primitive_cone_add(radius1=0.5, depth=1.0, vertices=32)
    elif k == "capsule":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, segments=24, ring_count=12)
    elif k in ("torus", "ring"):
        bpy.ops.mesh.primitive_torus_add(major_radius=0.36, minor_radius=0.12,
                                         major_segments=36, minor_segments=14)
    elif k == "plane":
        bpy.ops.mesh.primitive_plane_add(size=1.0)
    else:  # cube, rounded_cube, line, arrow-shaft, fallback
        bpy.ops.mesh.primitive_cube_add(size=1.0)
    return bpy.context.active_object


def _join(objs):
    objs = [o for o in objs if o]
    if not objs:
        return None
    if len(objs) == 1:
        return objs[0]
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    return bpy.context.active_object


# ── composed hero recipes (return a list of objects, roughly within a unit box) ──
def _compose(asset_id: str):
    aid = asset_id
    if aid == "generic_cell":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, segments=32, ring_count=16)
        membrane = bpy.context.active_object
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.18, location=(0.08, 0, 0.05))
        return [membrane, bpy.context.active_object]
    if aid == "engine_block_simplified":
        bpy.ops.mesh.primitive_cube_add(size=1.0)
        block = bpy.context.active_object
        block.scale = (1.0, 0.7, 0.55)
        cyls = []
        for i in range(4):
            x = -0.33 + i * 0.22
            bpy.ops.mesh.primitive_cylinder_add(radius=0.08, depth=0.3, location=(x, 0.0, 0.42))
            cyls.append(bpy.context.active_object)
        return [block] + cyls
    if aid in ("gear_small", "gear_medium", "gear_large", "gear_pair", "turbine_wheel", "fan_blade"):
        bpy.ops.mesh.primitive_cylinder_add(radius=0.42, depth=0.16, vertices=12)
        body = bpy.context.active_object
        teeth = []
        for i in range(12):
            a = i * (2 * math.pi / 12)
            bpy.ops.mesh.primitive_cube_add(size=0.16, location=(0.46 * math.cos(a), 0.46 * math.sin(a), 0))
            teeth.append(bpy.context.active_object)
        return [body] + teeth
    if aid == "atom_nucleus":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5)
        return [bpy.context.active_object]
    if aid == "pendulum":
        bpy.ops.mesh.primitive_cylinder_add(radius=0.04, depth=0.8, location=(0, 0, 0.0))
        rod = bpy.context.active_object
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.18, location=(0, 0, -0.45))
        return [rod, bpy.context.active_object]
    if aid == "dna_double_helix":
        objs = []
        for t in range(10):
            z = -0.45 + t * 0.1
            a = t * 0.7
            for sgn in (1, -1):
                bpy.ops.mesh.primitive_uv_sphere_add(
                    radius=0.07, location=(0.22 * math.cos(a) * sgn, 0.22 * math.sin(a) * sgn, z))
                objs.append(bpy.context.active_object)
        return objs
    if aid == "arrow_straight" or aid == "force_arrow" or aid.startswith("arrow"):
        bpy.ops.mesh.primitive_cylinder_add(radius=0.12, depth=0.7, location=(0, 0, -0.1))
        shaft = bpy.context.active_object
        bpy.ops.mesh.primitive_cone_add(radius1=0.26, depth=0.4, location=(0, 0, 0.45))
        return [shaft, bpy.context.active_object]
    return None


def _build(entry):
    composed = _compose(entry["assetId"])
    obj = _join(composed) if composed else _prim(entry.get("fallbackPrimitive"))
    if obj is None:
        raise RuntimeError("no geometry")
    # size to the target bounding box (metres), then seat by pivot
    dims = entry.get("scaleMeters") or [0.2, 0.2, 0.2]
    dims = [max(float(d), 0.01) for d in dims]
    bpy.context.view_layer.update()
    obj.dimensions = Vector(dims)
    bpy.context.view_layer.update()
    bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs = [v.x for v in bb]; ys = [v.y for v in bb]; zs = [v.z for v in bb]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    if (entry.get("pivot") or "center_bottom") == "center_bottom":
        obj.location -= Vector((cx, cy, min(zs)))            # centre X/Y, rest on z=0
    else:
        obj.location -= Vector((cx, cy, (min(zs) + max(zs)) / 2))
    bpy.context.view_layer.update()
    _material(obj, entry)
    return obj


def _material(obj, entry):
    rgba = _ID_COLOR.get(entry["assetId"]) or _CAT_COLOR.get(entry.get("category"), (0.7, 0.7, 0.72, 1))
    mat = bpy.data.materials.new(name=f"SPATAIL_{entry['assetId']}")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = rgba
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 0.55
        if entry["assetId"] in _EMISSIVE:
            for ename in ("Emission Color", "Emission"):
                if ename in bsdf.inputs:
                    try:
                        bsdf.inputs[ename].default_value = rgba
                    except Exception:
                        pass
            if "Emission Strength" in bsdf.inputs:
                bsdf.inputs["Emission Strength"].default_value = 2.0
    mat.diffuse_color = rgba
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def _export_glb(path: Path):
    try:
        bpy.ops.preferences.addon_enable(module="io_scene_gltf2")
    except Exception:
        pass
    common = dict(filepath=str(path), export_format="GLB", export_yup=True, export_apply=True)
    for extra in (dict(use_visible=True), {}):
        try:
            bpy.ops.export_scene.gltf(**common, **extra)
            return path.exists()
        except TypeError:
            continue
    return False


def _export_usdz(path: Path):
    rna = set(bpy.ops.wm.usd_export.get_rna_type().properties.keys())
    desired = {
        "filepath": str(path), "selected_objects_only": False, "export_uvmaps": True,
        "export_normals": True, "export_materials": True, "export_meshes": True,
        "generate_preview_surface": True, "convert_orientation": True,
        "export_global_forward_selection": "NEGATIVE_Z", "export_global_up_selection": "Y",
        "meters_per_unit": 1.0, "root_prim_path": "/Scene", "overwrite_textures": True,
    }
    kwargs = {k: v for k, v in desired.items() if k in rna}
    kwargs["filepath"] = str(path)
    try:
        bpy.ops.wm.usd_export(**kwargs)
        return path.exists()
    except Exception as e:  # noqa: BLE001
        print(f"[bake] USDZ export failed for {path.name}: {e!r}")
        return False


def main():
    spec_path = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else sys.argv[-1]
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    root = Path(spec["repoRoot"])
    lib_dir = root / "public" / "assets" / "spatail-library"
    baked, failed = [], []

    for entry in spec["assets"]:
        aid = entry["assetId"]
        try:
            _clear()
            _build(entry)
            cat_dir = lib_dir / entry["category"]
            cat_dir.mkdir(parents=True, exist_ok=True)
            glb = cat_dir / f"{aid}.glb"
            usdz = cat_dir / f"{aid}.usdz"
            ok_glb = _export_glb(glb)
            ok_usdz = _export_usdz(usdz)
            if not ok_glb:
                raise RuntimeError("GLB export failed")
            baked.append({"assetId": aid, "category": entry["category"],
                          "glb": str(glb), "usdz": str(usdz) if ok_usdz else "",
                          "scaleMeters": entry.get("scaleMeters")})
            print(f"[bake] {aid}: GLB{'+USDZ' if ok_usdz else ''}")
        except Exception as e:  # noqa: BLE001
            failed.append({"assetId": aid, "error": str(e)})
            print(f"[bake] FAILED {aid}: {e}")

    Path(spec["result_path"]).write_text(
        json.dumps({"ok": True, "baked": baked, "failed": failed,
                    "n_baked": len(baked), "n_failed": len(failed)}, indent=2),
        encoding="utf-8")
    print(f"[bake] done: {len(baked)} baked, {len(failed)} failed")


main()
