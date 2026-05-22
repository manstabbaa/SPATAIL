"""
spatail_treat_mesh.py — generic mesh treatment for any imported CAD.

See skills/spatail-treat-mesh/SKILL.md for the architectural rationale.

ENTRY POINT:
    exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_treat_mesh.py").read())
    manifest = treat_mesh(
        source=r"path/to/asset.obj",  # OBJ/STL/FBX/GLB; or None to use scene
        asset_id="v10_engine",
        out_dir=r"C:/SPATAIL_MAX/assets_processed/treated/v10_engine",
        clean_scene=True,               # wipe existing scene before import
    )

The 7 stages run in order. Each writes a section into the returned
manifest. The final JSON is also written to <out_dir>/<asset_id>.treatment.json.

DESIGN PRINCIPLES:
  - Mesh treatment is GEOMETRY-ONLY. No semantic labelling. No
    materials. No animation. No rendering. Those are downstream skills.
  - Stages are independently auditable: each emits its own dict.
  - The pivot decision is the load-bearing step — when downstream rigs
    use Damped Track or parenting, they need every object's origin to
    be its natural pivot.
"""

import bpy, json, math, os, sys
from datetime import datetime, timezone
from mathutils import Vector, Matrix


# ============================================================================
# Stage 0: scene reset
# ============================================================================

def clean_scene():
    """Remove all data-blocks for a fresh start."""
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)
    for img in list(bpy.data.images):
        try: bpy.data.images.remove(img)
        except Exception: pass


# ============================================================================
# Stage 1: import & sanitize
# ============================================================================

def stage_import(source, asset_id):
    """Import the source file (extension-dispatched) and bake transforms."""
    log = {"source": source, "imported_objects": [], "errors": []}
    if source is None:
        log["mode"] = "use_existing_scene"
        return log
    ext = os.path.splitext(source)[1].lower()
    log["mode"] = f"import_{ext.lstrip('.')}"
    pre = set(o.name for o in bpy.data.objects)
    try:
        if ext == ".obj":
            bpy.ops.wm.obj_import(filepath=source)
        elif ext == ".stl":
            bpy.ops.wm.stl_import(filepath=source)
        elif ext == ".fbx":
            bpy.ops.import_scene.fbx(filepath=source)
        elif ext in (".glb", ".gltf"):
            bpy.ops.import_scene.gltf(filepath=source)
        else:
            log["errors"].append(f"unsupported extension: {ext}")
            return log
    except Exception as e:
        log["errors"].append(f"import failed: {e}")
        return log
    new_objs = [o for o in bpy.data.objects if o.name not in pre]
    log["imported_objects"] = [o.name for o in new_objs]

    # Apply transforms on mesh objects so mesh data == world data.
    # (Many OBJ files import with rotation_euler set to align Y-up to Z-up;
    # we want geometry baked into the data so downstream code doesn't have
    # to deal with parent transforms.)
    applied = []
    for obj in new_objs:
        if obj.type != "MESH": continue
        try:
            for o in bpy.context.selected_objects: o.select_set(False)
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
            applied.append(obj.name)
        except Exception as e:
            log["errors"].append(f"transform_apply failed for {obj.name}: {e}")
    log["transform_applied"] = applied
    return log


# ============================================================================
# Stage 2: topology audit
# ============================================================================

def world_bbox(obj):
    lo = Vector((float("inf"),)*3); hi = Vector((-float("inf"),)*3)
    for c in obj.bound_box:
        p = obj.matrix_world @ Vector(c)
        lo = Vector(map(min, lo, p)); hi = Vector(map(max, hi, p))
    return lo, hi


def count_mesh_islands(obj):
    """Count connected components in the mesh (depth-first union-find on edges)."""
    if obj.type != "MESH": return 0
    parent = list(range(len(obj.data.vertices)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb
    for edge in obj.data.edges:
        union(edge.vertices[0], edge.vertices[1])
    roots = {find(i) for i in range(len(obj.data.vertices))}
    return len(roots)


def stage_topology(asset_objs):
    """Per-object audit + asset-level rollup."""
    objs = []
    asset_lo = Vector((float("inf"),)*3); asset_hi = Vector((-float("inf"),)*3)
    for obj in asset_objs:
        if obj.type != "MESH": continue
        lo, hi = world_bbox(obj)
        asset_lo = Vector(map(min, asset_lo, lo))
        asset_hi = Vector(map(max, asset_hi, hi))
        size = hi - lo
        islands = count_mesh_islands(obj)
        rec = {
            "name": obj.name,
            "vertex_count": len(obj.data.vertices),
            "edge_count": len(obj.data.edges),
            "face_count": len(obj.data.polygons),
            "mesh_island_count": islands,
            "bbox_lo": [round(c, 4) for c in lo],
            "bbox_hi": [round(c, 4) for c in hi],
            "bbox_size": [round(c, 4) for c in size],
            "bbox_diagonal": round(size.length, 4),
        }
        objs.append(rec)
    size = asset_hi - asset_lo
    longest = max(size) if size.length > 0 else 0
    # Unit guess: CAD usually mm or cm. >500 likely mm, <500 likely cm.
    unit_guess = "mm" if longest > 500 else "cm" if longest > 1 else "m"
    return {
        "object_count": len(objs),
        "objects": objs,
        "asset_bbox_lo":   [round(c, 4) for c in asset_lo],
        "asset_bbox_hi":   [round(c, 4) for c in asset_hi],
        "asset_bbox_size": [round(c, 4) for c in size],
        "asset_longest_extent": round(longest, 4),
        "unit_guess": unit_guess,
        "objects_with_multiple_islands":
            [o["name"] for o in objs if o["mesh_island_count"] > 1],
    }


# ============================================================================
# Stage 3: segmentation
# ============================================================================

def stage_segmentation(asset_objs):
    """Separate any object containing >1 mesh island into one object per island.

    Crucial because a "single OBJ object" often contains many disconnected
    rigid bodies (e.g. one mesh named 'rod' containing 10 separate rods).
    """
    log = {"separated": []}
    multi = [o for o in asset_objs if o.type == "MESH" and count_mesh_islands(o) > 1]
    for obj in multi:
        pre = set(b.name for b in bpy.data.objects)
        for o in bpy.context.selected_objects: o.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.mesh.separate(type="LOOSE")
        post = [b for b in bpy.data.objects if b.name not in pre]
        log["separated"].append({"from": obj.name, "into_count": len(post),
                                  "new_names": [b.name for b in post]})
    return log


# ============================================================================
# Stage 4: principal geometry (per-part PCA)
# ============================================================================

def pca_3x3(points):
    """Pure-Python PCA — returns (centroid, axes:[Vec3], eigvals:[float])
    sorted by descending eigenvalue. Power iteration with deflation; no numpy."""
    n = len(points)
    if n < 4: return Vector((0,0,0)), [], []
    cx = cy = cz = 0.0
    for p in points:
        cx += p.x; cy += p.y; cz += p.z
    cx /= n; cy /= n; cz /= n
    centroid = Vector((cx, cy, cz))
    M = [[0.0]*3 for _ in range(3)]
    for p in points:
        dx, dy, dz = p.x - cx, p.y - cy, p.z - cz
        M[0][0] += dx*dx; M[0][1] += dx*dy; M[0][2] += dx*dz
        M[1][1] += dy*dy; M[1][2] += dy*dz
        M[2][2] += dz*dz
    M[1][0] = M[0][1]; M[2][0] = M[0][2]; M[2][1] = M[1][2]
    for i in range(3):
        for j in range(3):
            M[i][j] /= n
    axes = []
    A = [row[:] for row in M]
    for _ in range(3):
        v = [1.0, 1.0, 1.0]
        for _it in range(80):
            nv = [sum(A[i][j] * v[j] for j in range(3)) for i in range(3)]
            mag = math.sqrt(sum(x*x for x in nv)) or 1.0
            v = [x / mag for x in nv]
        Av = [sum(A[i][j] * v[j] for j in range(3)) for i in range(3)]
        eig = sum(v[i] * Av[i] for i in range(3))
        axes.append((Vector(v), eig))
        for i in range(3):
            for j in range(3):
                A[i][j] -= eig * v[i] * v[j]
    axes.sort(key=lambda x: -x[1])
    return centroid, [a for a, _ in axes], [e for _, e in axes]


def classify_shape(eigvals, vert_count):
    """Classify shape from PCA eigenvalue spectrum."""
    if not eigvals or len(eigvals) < 3 or eigvals[0] < 1e-9:
        return "degenerate"
    r1 = eigvals[1] / eigvals[0]   # secondary / primary
    r2 = eigvals[2] / eigvals[0]   # tertiary / primary
    # rod-like: long & thin (small r1 + small r2)
    if r1 < 0.25 and r2 < 0.25:
        return "rod-like"
    # disc-like: planar (large r1, small r2)
    if r1 > 0.5 and r2 < 0.2:
        return "disc-like"
    # cylinder-like: long with rotational symmetry (small r1, r2 ≈ r1)
    if r1 < 0.35 and abs(r1 - r2) < 0.1:
        return "cylinder-like"
    # blob: comparable spread on all axes
    if r1 > 0.5 and r2 > 0.4:
        return "blob"
    return "irregular"


def stage_principal_geometry(asset_objs):
    """Per-part PCA, eigenvalues, shape class."""
    out = []
    for obj in asset_objs:
        if obj.type != "MESH" or not obj.data.vertices: continue
        pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
        centroid, axes, eigvals = pca_3x3(pts)
        # Bbox centre is more stable than vertex centroid for unbalanced parts.
        lo, hi = world_bbox(obj)
        bbox_centre = (lo + hi) * 0.5
        shape = classify_shape(eigvals, len(pts))
        out.append({
            "name": obj.name,
            "vertex_centroid":  [round(c, 4) for c in centroid],
            "bbox_centre":      [round(c, 4) for c in bbox_centre],
            "principal_axis":   [round(c, 4) for c in axes[0]] if axes else None,
            "secondary_axis":   [round(c, 4) for c in axes[1]] if len(axes) > 1 else None,
            "tertiary_axis":    [round(c, 4) for c in axes[2]] if len(axes) > 2 else None,
            "eigvals": [round(e, 6) for e in eigvals],
            "aspect_secondary": round(eigvals[1] / eigvals[0], 4) if eigvals and eigvals[0] > 1e-9 else None,
            "aspect_tertiary":  round(eigvals[2] / eigvals[0], 4) if eigvals and eigvals[0] > 1e-9 else None,
            "shape_class": shape,
        })
    return {"parts": out}


# ============================================================================
# Stage 5: pivot detection
# ============================================================================

def detect_pivot(obj, pca_record):
    """Return (pivot_world_xyz, reason) for this part based on its shape class."""
    pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
    if not pts:
        return Vector((0,0,0)), "no_vertices"
    bbox_centre = Vector(pca_record["bbox_centre"])
    shape = pca_record["shape_class"]
    axis = Vector(pca_record["principal_axis"]) if pca_record["principal_axis"] else None

    if shape == "rod-like" and axis is not None:
        # Pivot = the small end (lower cross-sectional spread). Project
        # all points onto principal axis, take extremes, measure perp
        # spread at each end → smaller spread is the small end.
        projs = sorted([(p.dot(axis), p) for p in pts], key=lambda x: x[0])
        n = len(projs)
        k = max(3, int(n * 0.06))
        lo_pts = [p for _, p in projs[:k]]
        hi_pts = [p for _, p in projs[-k:]]
        lo_centre = sum(lo_pts, Vector((0,0,0))) / len(lo_pts)
        hi_centre = sum(hi_pts, Vector((0,0,0))) / len(hi_pts)
        # Cross-sectional spread at each end (perpendicular to axis)
        def perp_spread(centre, sample):
            s = 0.0; m = 0
            for _, p in sample:
                d = p - centre
                d_perp = d - axis * d.dot(axis)
                s += d_perp.length_squared; m += 1
            return math.sqrt(s / max(1, m))
        spread_lo = perp_spread(lo_centre, projs[:max(3, n // 5)])
        spread_hi = perp_spread(hi_centre, projs[-max(3, n // 5):])
        if spread_lo < spread_hi:
            return lo_centre, f"rod_small_end (perp_spread lo={spread_lo:.3f} < hi={spread_hi:.3f})"
        return hi_centre, f"rod_small_end (perp_spread hi={spread_hi:.3f} < lo={spread_lo:.3f})"

    if shape == "cylinder-like" and axis is not None:
        # Pivot = centre of one circular face. For a cylinder, both ends
        # are equivalent rings; default to the LOW projection end (caller
        # can flip if needed).
        projs = sorted([(p.dot(axis), p) for p in pts], key=lambda x: x[0])
        k = max(3, int(len(projs) * 0.05))
        lo_pts = [p for _, p in projs[:k]]
        lo_centre = sum(lo_pts, Vector((0,0,0))) / len(lo_pts)
        return lo_centre, "cylinder_low_face_centre"

    if shape == "disc-like":
        return bbox_centre, "disc_centre"

    # default fallback
    return bbox_centre, f"bbox_centre ({shape})"


def stage_pivots(asset_objs, principal_geometry):
    pca_by_name = {r["name"]: r for r in principal_geometry["parts"]}
    out = []
    for obj in asset_objs:
        if obj.type != "MESH" or obj.name not in pca_by_name: continue
        pivot, reason = detect_pivot(obj, pca_by_name[obj.name])
        out.append({
            "name": obj.name,
            "pivot_world": [round(c, 4) for c in pivot],
            "pivot_reason": reason,
            "shape_class": pca_by_name[obj.name]["shape_class"],
        })
    return {"parts": out}


# ============================================================================
# Stage 6: origin normalization
# ============================================================================

def set_origin_world(obj, new_origin_world):
    """Shift mesh data so the object origin lands at new_origin_world,
    while the geometry stays in the same world position."""
    current = obj.matrix_world.translation.copy()
    delta_world = Vector(new_origin_world) - current
    delta_local = obj.matrix_world.inverted().to_3x3() @ delta_world
    obj.data.transform(Matrix.Translation(-delta_local))
    obj.matrix_world.translation = current + delta_world


def stage_normalize(asset_objs, pivots):
    pivot_by_name = {p["name"]: p for p in pivots["parts"]}
    moves = []
    for obj in asset_objs:
        if obj.type != "MESH" or obj.name not in pivot_by_name: continue
        old = list(obj.matrix_world.translation)
        target = Vector(pivot_by_name[obj.name]["pivot_world"])
        set_origin_world(obj, target)
        new = list(obj.matrix_world.translation)
        moves.append({
            "name": obj.name,
            "old_origin": [round(c, 4) for c in old],
            "new_origin": [round(c, 4) for c in new],
            "delta": [round(new[i] - old[i], 4) for i in range(3)],
        })
    return {"moves": moves}


# ============================================================================
# Stage 7: write manifest
# ============================================================================

def write_manifest(manifest, out_dir, asset_id):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{asset_id}.treatment.json").replace("\\", "/")
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    return path


# ============================================================================
# Orchestrator
# ============================================================================

def treat_mesh(source=None, asset_id="asset", out_dir=None, clean_scene_first=True):
    """Run the 7-stage treatment. Returns manifest dict; also writes JSON."""
    if out_dir is None:
        out_dir = f"C:/SPATAIL_MAX/assets_processed/treated/{asset_id}"

    if clean_scene_first and source is not None:
        clean_scene()

    pre = set(o.name for o in bpy.data.objects)
    stage1 = stage_import(source, asset_id)
    if source is None:
        new_objs = list(bpy.data.objects)
    else:
        new_objs = [o for o in bpy.data.objects if o.name in stage1["imported_objects"]
                    or o.name not in pre]

    # Re-query objects after import (may have been mutated)
    asset_objs = [o for o in new_objs if o.type == "MESH"]

    stage2 = stage_topology(asset_objs)
    stage3 = stage_segmentation(asset_objs)
    # Refresh after separation — separation creates new objects
    asset_objs = [o for o in bpy.data.objects if o.type == "MESH"]
    stage4 = stage_principal_geometry(asset_objs)
    stage5 = stage_pivots(asset_objs, stage4)
    stage6 = stage_normalize(asset_objs, stage5)

    manifest = {
        "assetId": asset_id,
        "schemaVersion": "0.1.0-spatail-treat-mesh",
        "treatedAt": datetime.now(timezone.utc).isoformat(),
        "stages": {
            "1_import": stage1,
            "2_topology": stage2,
            "3_segmentation": stage3,
            "4_principal_geometry": stage4,
            "5_pivots": stage5,
            "6_normalization": stage6,
        },
        "summary": {
            "final_part_count": len([o for o in bpy.data.objects if o.type == "MESH"]),
            "imported_part_count": len(stage1.get("imported_objects", [])),
            "split_count": sum(s["into_count"] for s in stage3["separated"]),
            "shape_class_histogram": _shape_histogram(stage4["parts"]),
            "asset_bbox_size": stage2["asset_bbox_size"],
            "unit_guess": stage2["unit_guess"],
        },
    }
    out_path = write_manifest(manifest, out_dir, asset_id)
    manifest["_writtenTo"] = out_path
    print(f"[treat_mesh] manifest → {out_path}")
    return manifest


def _shape_histogram(part_records):
    h = {}
    for r in part_records:
        h[r["shape_class"]] = h.get(r["shape_class"], 0) + 1
    return h


print("[spatail_treat_mesh] module loaded.")
