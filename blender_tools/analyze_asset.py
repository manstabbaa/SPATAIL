"""Headless Blender script.

Usage (from Node pipeline):
  blender --background --factory-startup --python analyze_asset.py -- <input_path> <output_dir> <asset_id>

Behavior:
  - Opens a clean Blender scene
  - Imports the input file using the right operator for its extension
  - Computes bounding box, dimensions, object count, vertex/face totals
  - Exports a normalized .glb to <output_dir>/<asset_id>.glb
  - Writes <output_dir>/<asset_id>.analysis.json with metadata the Node
    pipeline reads back into the Spatial Scene Contract.

If an import fails (operator missing, file corrupt, etc.) the script still
writes an analysis.json with status="failed" and a reason, so the pipeline
can carry on with the other assets.
"""

import bpy
import sys
import os
import json
import traceback
import math


SUPPORTED_EXTENSIONS = {
    ".glb", ".gltf",
    ".obj",
    ".fbx",
    ".stl",
    ".usd", ".usdz", ".usda", ".usdc",
    ".step", ".stp",
    ".iges", ".igs",
    ".ply",
}


def parse_args():
    if "--" not in sys.argv:
        sys.exit("error: missing '--' separator before script args")
    idx = sys.argv.index("--")
    rest = sys.argv[idx + 1:]
    if len(rest) < 3:
        sys.exit("error: expected <input_path> <output_dir> <asset_id>")
    return rest[0], rest[1], rest[2]


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def operator_exists(dotted):
    """Probe whether bpy.ops.<dotted> is a real, callable operator.

    bpy.ops.* returns a wrapper for any attribute access, so a plain
    hasattr / eval cannot tell apart real operators from typos. Calling
    .idname() raises AttributeError for the placeholder wrappers but
    succeeds for real ones.
    """
    try:
        op = eval(f"bpy.ops.{dotted}")
        op.idname()
        return True
    except Exception:
        return False


# Known CAD-import addon module names across Blender versions and the
# Extensions Platform. We *try* to enable each in turn; missing modules
# are skipped silently. If a STEP-capable addon is on the system, this
# turns it on for us before we attempt the import.
CAD_ADDON_CANDIDATES = [
    "io_import_step",
    "io_scene_step",
    "io_mesh_step",
    "STEPper",
    "bl_ext.user_default.step_importer",
    "bl_ext.blender_org.step_importer",
]


def try_enable_cad_addons():
    enabled = []
    for mod in CAD_ADDON_CANDIDATES:
        try:
            bpy.ops.preferences.addon_enable(module=mod)
            enabled.append(mod)
        except Exception:
            continue
    return enabled


def _wrap_op(dotted):
    """Return a callable that invokes bpy.ops.<dotted>(filepath=...)."""
    return lambda **k: eval(f"bpy.ops.{dotted}")(**k)


def try_ops(operators, filepath):
    """Try a list of operator callables in order until one succeeds."""
    last_err = None
    for desc, op in operators:
        try:
            op(filepath=filepath)
            return desc
        except Exception as e:
            last_err = (desc, e)
    if last_err is None:
        raise RuntimeError("no importers were available to try")
    raise RuntimeError(
        f"all importers failed; last error from {last_err[0]}: {last_err[1]}"
    )


def import_file(filepath):
    ext = os.path.splitext(filepath)[1].lower()

    if ext in (".glb", ".gltf"):
        return try_ops([
            ("import_scene.gltf", bpy.ops.import_scene.gltf),
        ], filepath)

    if ext == ".obj":
        ops = []
        if hasattr(bpy.ops.wm, "obj_import"):
            ops.append(("wm.obj_import", bpy.ops.wm.obj_import))
        if hasattr(bpy.ops.import_scene, "obj"):
            ops.append(("import_scene.obj", bpy.ops.import_scene.obj))
        return try_ops(ops, filepath)

    if ext == ".fbx":
        return try_ops([
            ("import_scene.fbx", bpy.ops.import_scene.fbx),
        ], filepath)

    if ext == ".stl":
        ops = []
        if hasattr(bpy.ops.wm, "stl_import"):
            ops.append(("wm.stl_import", bpy.ops.wm.stl_import))
        if hasattr(bpy.ops.import_mesh, "stl"):
            ops.append(("import_mesh.stl", bpy.ops.import_mesh.stl))
        return try_ops(ops, filepath)

    if ext == ".ply":
        ops = []
        if hasattr(bpy.ops.wm, "ply_import"):
            ops.append(("wm.ply_import", bpy.ops.wm.ply_import))
        if hasattr(bpy.ops.import_mesh, "ply"):
            ops.append(("import_mesh.ply", bpy.ops.import_mesh.ply))
        return try_ops(ops, filepath)

    if ext in (".usd", ".usda", ".usdc", ".usdz"):
        return try_ops([
            ("wm.usd_import", bpy.ops.wm.usd_import),
        ], filepath)

    if ext in (".step", ".stp"):
        try_enable_cad_addons()
        candidates = [
            "wm.step_import",
            "import_scene.step",
            "import_scene.occ_import_step",
            "import_cad.step",
            "import_mesh.step",
        ]
        ops = []
        for dotted in candidates:
            if operator_exists(dotted):
                ops.append((dotted, _wrap_op(dotted)))
        if not ops:
            raise RuntimeError(
                "no STEP importer is available in this Blender build. "
                "Install the official 'STEP Importer' Blender extension "
                "(Edit > Preferences > Get Extensions), or pre-convert "
                "the file to STL/OBJ/GLB."
            )
        return try_ops(ops, filepath)

    if ext in (".iges", ".igs"):
        try_enable_cad_addons()
        candidates = [
            "wm.iges_import",
            "import_scene.iges",
            "import_scene.occ_import_iges",
        ]
        ops = []
        for dotted in candidates:
            if operator_exists(dotted):
                ops.append((dotted, _wrap_op(dotted)))
        if not ops:
            raise RuntimeError("no IGES importer is available in this Blender build")
        return try_ops(ops, filepath)

    raise RuntimeError(f"unsupported extension: {ext}")


def scene_metrics():
    """Walk all mesh objects and compute aggregate stats + world-space bbox."""
    minv = [math.inf] * 3
    maxv = [-math.inf] * 3
    vert_total = 0
    face_total = 0
    objects = []

    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        mesh = obj.data
        verts = len(mesh.vertices)
        faces = len(mesh.polygons)
        vert_total += verts
        face_total += faces

        obj_min = [math.inf] * 3
        obj_max = [-math.inf] * 3
        for c in obj.bound_box:
            world = obj.matrix_world @ _vec3(c)
            for i in range(3):
                obj_min[i] = min(obj_min[i], world[i])
                obj_max[i] = max(obj_max[i], world[i])
                minv[i] = min(minv[i], world[i])
                maxv[i] = max(maxv[i], world[i])

        objects.append({
            "name": obj.name,
            "type": obj.type,
            "vertexCount": verts,
            "faceCount": faces,
            "bbox": {
                "min": obj_min,
                "max": obj_max,
            },
            "location": list(obj.matrix_world.to_translation()),
        })

    if vert_total == 0:
        return {
            "objectCount": 0,
            "vertexCount": 0,
            "faceCount": 0,
            "bbox": None,
            "dimensionsMeters": None,
            "objects": [],
        }

    dims = [maxv[i] - minv[i] for i in range(3)]
    return {
        "objectCount": len(objects),
        "vertexCount": vert_total,
        "faceCount": face_total,
        "bbox": {"min": minv, "max": maxv},
        "dimensionsMeters": dims,
        "objects": objects,
    }


def _vec3(c):
    from mathutils import Vector
    return Vector((c[0], c[1], c[2]))


def export_glb(output_path):
    bpy.ops.export_scene.gltf(
        filepath=output_path,
        export_format="GLB",
        export_apply=True,
        export_yup=True,
    )


def main():
    input_path, output_dir, asset_id = parse_args()
    os.makedirs(output_dir, exist_ok=True)
    analysis_path = os.path.join(output_dir, f"{asset_id}.analysis.json")
    glb_path = os.path.join(output_dir, f"{asset_id}.glb")

    result = {
        "assetId": asset_id,
        "sourcePath": input_path,
        "extension": os.path.splitext(input_path)[1].lower(),
        "importer": None,
        "status": "failed",
        "reason": None,
        "processedPath": None,
        "metrics": None,
    }

    try:
        if not os.path.isfile(input_path):
            raise RuntimeError(f"input not found: {input_path}")
        reset_scene()
        importer = import_file(input_path)
        result["importer"] = importer

        metrics = scene_metrics()
        result["metrics"] = metrics

        if metrics["objectCount"] == 0:
            raise RuntimeError("import produced no mesh objects")

        export_glb(glb_path)
        result["status"] = "ok"
        result["processedPath"] = glb_path
    except Exception as e:
        result["reason"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()
    finally:
        with open(analysis_path, "w", encoding="utf-8") as fp:
            json.dump(result, fp, indent=2, default=str)
        print(f"[analyze_asset] wrote {analysis_path}")


if __name__ == "__main__":
    main()
