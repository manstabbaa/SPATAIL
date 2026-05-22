"""Headless mesh audit.

Usage:
  blender --background --factory-startup --python audit_mesh.py -- <input_path>

What it inspects on the source mesh BEFORE any fixes:
  - vertex / face / edge counts
  - bounding box + assumed units (mm vs m)
  - non-manifold edge count
  - loose-vertex count
  - duplicate-vertex count (within a small distance)
  - face winding inconsistency (signed-area test on a sampled subset)
  - sharp-edge ratio (proxy for tessellation quality)
  - material slot count

The report is written to <input_path>.audit.json next to the source and
also printed to stdout. The next pipeline step (segmentation) consumes
the report to decide whether to apply normal-fix / weld / scale-fix.
"""

import bpy
import bmesh
import sys
import os
import json
import math
from mathutils import Vector


def parse_args():
    if "--" not in sys.argv:
        sys.exit("error: pass args after '--'")
    rest = sys.argv[sys.argv.index("--") + 1:]
    if not rest:
        sys.exit("error: <input_path> required")
    return rest[0]


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_mesh(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=path)
        else:
            bpy.ops.import_scene.obj(filepath=path)
    elif ext in (".gltf", ".glb"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".stl":
        if hasattr(bpy.ops.wm, "stl_import"):
            bpy.ops.wm.stl_import(filepath=path)
        else:
            bpy.ops.import_mesh.stl(filepath=path)
    else:
        sys.exit(f"unsupported extension: {ext}")


def join_meshes():
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        return None
    if len(meshes) == 1:
        bpy.context.view_layer.objects.active = meshes[0]
        return meshes[0]
    for o in bpy.data.objects:
        o.select_set(False)
    target = meshes[0]
    for m in meshes:
        m.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.join()
    return target


def world_bbox(obj):
    minv = [math.inf] * 3
    maxv = [-math.inf] * 3
    for c in obj.bound_box:
        w = obj.matrix_world @ Vector((c[0], c[1], c[2]))
        for i in range(3):
            minv[i] = min(minv[i], w[i])
            maxv[i] = max(maxv[i], w[i])
    return minv, maxv


def audit(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        non_manifold_edges = sum(1 for e in bm.edges if not e.is_manifold)
        wire_edges = sum(1 for e in bm.edges if e.is_wire)
        boundary_edges = sum(1 for e in bm.edges if e.is_boundary)

        # Duplicate-vertex probe (cheap N^2 scan on a small random sample).
        sample_size = min(2000, len(bm.verts))
        sample = list(bm.verts)[:sample_size]
        dup_count = 0
        DUP_THRESH = 1e-5
        for i in range(len(sample)):
            for j in range(i + 1, min(i + 50, len(sample))):
                if (sample[i].co - sample[j].co).length < DUP_THRESH:
                    dup_count += 1
                    break

        # Face-winding consistency on a sampled subset. We pick face pairs
        # that share an edge and check whether their normal dot products
        # are positive. Negative-dot adjacent pairs likely have one
        # inverted winding.
        inv_neighbours = 0
        checked_pairs = 0
        for e in list(bm.edges)[:5000]:
            if len(e.link_faces) != 2:
                continue
            f0, f1 = e.link_faces
            if f0.normal.length < 1e-8 or f1.normal.length < 1e-8:
                continue
            checked_pairs += 1
            if f0.normal.dot(f1.normal) < -0.5:
                inv_neighbours += 1

        # Sharp-edge ratio — proxy for tessellation quality. Guard against
        # zero-length normals (degenerate faces); we count them separately
        # because they're themselves a red flag in CAD tessellation output.
        sharp_edges = 0
        degenerate_face_normals = 0
        for e in list(bm.edges)[:8000]:
            if len(e.link_faces) == 2:
                a, b = e.link_faces
                if a.normal.length < 1e-8 or b.normal.length < 1e-8:
                    degenerate_face_normals += 1
                    continue
                try:
                    if a.normal.angle(b.normal) > math.radians(70):
                        sharp_edges += 1
                except Exception:
                    degenerate_face_normals += 1

        verts = len(bm.verts)
        faces = len(bm.faces)
        edges = len(bm.edges)
    finally:
        bm.free()

    minv, maxv = world_bbox(obj)
    size = [maxv[i] - minv[i] for i in range(3)]
    longest = max(size)
    unit_guess = "mm" if longest > 10 else "m"

    return {
        "vertexCount": verts,
        "faceCount": faces,
        "edgeCount": edges,
        "bbox": {"min": minv, "max": maxv, "sizeMeters_raw": size,
                  "longestAxis_raw": longest},
        "assumedUnit": unit_guess,
        "nonManifoldEdges": non_manifold_edges,
        "wireEdges": wire_edges,
        "boundaryEdges": boundary_edges,
        "duplicateVertexHits": dup_count,
        "invertedNeighbourFacePairs": inv_neighbours,
        "facePairsChecked": checked_pairs,
        "sharpEdgesSampled": sharp_edges,
        "degenerateFaceNormalsSampled": degenerate_face_normals,
        "materialSlots": len(obj.data.materials),
        "verdicts": derive_verdicts(
            non_manifold_edges, boundary_edges, inv_neighbours,
            checked_pairs, dup_count, unit_guess,
        ),
    }


def derive_verdicts(non_manifold, boundary, inv_pairs, checked, dups, unit):
    out = []
    if unit == "mm":
        out.append("Source is in millimetres — pipeline must scale by 0.001 before export.")
    if inv_pairs > 0:
        ratio = inv_pairs / max(1, checked)
        out.append(
            f"{inv_pairs}/{checked} adjacent face pairs have inverted normals "
            f"({ratio:.1%}). Run normals_make_consistent + recalculate."
        )
    if non_manifold > 0:
        out.append(f"{non_manifold} non-manifold edges — expect render gaps unless materials are double-sided.")
    if boundary > 1000:
        out.append(f"{boundary} boundary edges — geometry is open / not water-tight (common for CAD tessellation).")
    if dups > 0:
        out.append(f"~{dups} duplicate vertices detected in sample — weld by distance before export.")
    if not out:
        out.append("Mesh is clean.")
    return out


def main():
    input_path = parse_args()
    if not os.path.isfile(input_path):
        sys.exit(f"input not found: {input_path}")
    reset_scene()
    import_mesh(input_path)
    obj = join_meshes()
    if not obj:
        sys.exit("no mesh objects after import")
    for o in list(bpy.data.objects):
        if o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)

    report = audit(obj)
    out_path = input_path + ".audit.json"
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, indent=2, default=str)
    print(f"[audit_mesh] wrote {out_path}")
    print(json.dumps(report["verdicts"], indent=2))


if __name__ == "__main__":
    main()
