"""inspect_asset.py — gather inspection metrics BEFORE any modification (bpy).

Everything here is read-only: counts, the source-frame bounding box, and a set of
"is this asset healthy?" booleans (UVs, materials, loose geometry, non-manifold
edges, plausible normals, fused vs multi-part). Heavy per-vertex checks
(non-manifold) are capped so a 5M-tri Meshy dump can't stall a worker.
"""
from __future__ import annotations

import bmesh
import bpy
from mathutils import Vector

# Above this total vertex count we skip the expensive bmesh manifold scan and
# report it as "unknown" rather than risk a multi-minute stall in a worker.
_MANIFOLD_SCAN_VERT_CAP = 600_000


def _mesh_objects(objs):
    return [o for o in objs if o.type == "MESH" and o.data]


def world_bbox(objs) -> dict:
    """Union world-space AABB over the given mesh objects (metres)."""
    meshes = _mesh_objects(objs)
    bpy.context.view_layer.update()
    pts = []
    for o in meshes:
        pts.extend(o.matrix_world @ Vector(c) for c in o.bound_box)
    if not pts:
        z = [0.0, 0.0, 0.0]
        return {"min": z, "max": z[:], "size": z[:], "center": z[:], "largest_dim": 0.0}
    xs = [p.x for p in pts]; ys = [p.y for p in pts]; zs = [p.z for p in pts]
    mn = [min(xs), min(ys), min(zs)]
    mx = [max(xs), max(ys), max(zs)]
    size = [mx[i] - mn[i] for i in range(3)]
    center = [(mx[i] + mn[i]) / 2 for i in range(3)]
    return {
        "min": [round(v, 6) for v in mn], "max": [round(v, 6) for v in mx],
        "size": [round(v, 6) for v in size], "center": [round(v, 6) for v in center],
        "largest_dim": round(max(size), 6),
    }


def inspect(objs, *, group_name: str, source_files: list, primary_source: str) -> dict:
    """Collect the full inspection record for the imported asset group."""
    meshes = _mesh_objects(objs)
    materials, images = _materials_and_images(meshes)

    tri = vert = 0
    has_uvs = False
    for o in meshes:
        me = o.data
        me.calc_loop_triangles()
        tri += len(me.loop_triangles)
        vert += len(me.vertices)
        if me.uv_layers:
            has_uvs = True

    loose, nonmanifold, normals_ok = _topology_health(meshes, vert)

    return {
        "asset_group": group_name,
        "source_files": list(source_files or []),
        "primary_source_file": primary_source,
        "imported_object_count": len(objs),
        "mesh_count": len(meshes),
        "material_count": len(materials),
        "texture_count": len(images),
        "triangle_count": tri,
        "vertex_count": vert,
        "original_bbox_m": world_bbox(objs),
        "has_uvs": has_uvs,
        "has_materials": len(materials) > 0,
        "loose_geometry_detected": loose,
        "non_manifold_detected": nonmanifold,
        "normals_appear_valid": normals_ok,
        "appears_fused": len(meshes) == 1,
        "appears_multi_part": len(meshes) > 1,
        "object_types": sorted({o.type for o in objs}),
    }


def _materials_and_images(meshes):
    materials, images = set(), set()
    for o in meshes:
        for slot in o.material_slots:
            mat = slot.material
            if not mat:
                continue
            materials.add(mat)
            if mat.use_nodes and mat.node_tree:
                for node in mat.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image:
                        images.add(node.image)
    return materials, images


def _topology_health(meshes, total_verts):
    """(loose_detected, non_manifold_detected_or_None, normals_appear_valid).

    non_manifold is None when skipped on a very heavy mesh (reported as "unknown").
    """
    loose = False
    nonmanifold = False
    normals_ok = True
    scan_manifold = total_verts <= _MANIFOLD_SCAN_VERT_CAP
    skipped_manifold = not scan_manifold

    for o in meshes:
        bm = bmesh.new()
        try:
            bm.from_mesh(o.data)
            # Loose: verts with no linked faces, or wire/loose edges.
            if any(len(v.link_faces) == 0 for v in bm.verts):
                loose = True
            elif any(len(e.link_faces) == 0 for e in bm.edges):
                loose = True
            if scan_manifold and not nonmanifold:
                if any(not e.is_manifold for e in bm.edges):
                    nonmanifold = True
            # Normals: a mesh with zero polygons, or all-zero face normals, is suspect.
            if o.data.polygons:
                n = o.data.polygons[0].normal
                if n.length < 1e-6:
                    normals_ok = False
            else:
                normals_ok = False
        except Exception:
            normals_ok = normals_ok          # leave health flags as-is on a bmesh error
        finally:
            bm.free()

    return loose, (None if skipped_manifold else nonmanifold), normals_ok
