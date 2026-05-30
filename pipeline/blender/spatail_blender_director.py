"""
spatail_blender_director.py — per-mesh pivot centering + per-part rest-pose
JSON export.

For every Mesh object in the scene:
  1. Snapshot original matrix_world (the "rest" the runtime trusts)
  2. Move origin to the local bbox center, preserving world position
  3. Recompute local geometry (bbox, principal axis, vertex/face counts)
  4. Find K nearest neighbors by bbox-center distance
  5. Write <out_dir>/<part_id>.rest.json + append to _index.json
  6. After all parts: write <out_dir>/_manifest.json

Idempotent. Re-running on an already-centered scene is a no-op for
geometry (the shift is 0) but rewrites the JSONs with fresh metadata.

Public entry points:
  direct_blender(asset_id, out_dir, ...)         → process scene, write JSONs
  restore_from_rest(rest_dir)                    → apply baked rest_transforms
                                                   back onto matching objects

See skills/spatail-blender-director/SKILL.md for the full design rationale.
"""
import bpy
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from mathutils import Vector, Matrix


SCHEMA_VERSION = "blender_director_v1"


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _world_bbox(obj):
    """World-space AABB of `obj` using its bound_box corners + matrix_world."""
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    mn = Vector((min(c.x for c in corners),
                 min(c.y for c in corners),
                 min(c.z for c in corners)))
    mx = Vector((max(c.x for c in corners),
                 max(c.y for c in corners),
                 max(c.z for c in corners)))
    return mn, mx


def _local_bbox(obj):
    """Local-space AABB from bound_box (no matrix_world applied)."""
    corners = [Vector(c) for c in obj.bound_box]
    mn = Vector((min(c.x for c in corners),
                 min(c.y for c in corners),
                 min(c.z for c in corners)))
    mx = Vector((max(c.x for c in corners),
                 max(c.y for c in corners),
                 max(c.z for c in corners)))
    return mn, mx


def _local_bbox_center(obj):
    mn, mx = _local_bbox(obj)
    return (mn + mx) * 0.5


def _principal_axis_local(obj, max_verts=2000):
    """PCA on (sub-sampled) local vertices. Returns the unit vector along
    the largest eigenvalue direction + that vector's length in local units."""
    me = obj.data
    n = len(me.vertices)
    if n == 0:
        return Vector((1, 0, 0)), 0.0
    # Sub-sample for speed on huge meshes
    step = max(1, n // max_verts)
    verts = [me.vertices[i].co for i in range(0, n, step)]
    # Centroid (after recentering this is ~origin, but be safe)
    cx = sum(v.x for v in verts) / len(verts)
    cy = sum(v.y for v in verts) / len(verts)
    cz = sum(v.z for v in verts) / len(verts)
    # Covariance matrix (3x3, symmetric)
    cxx = cxy = cxz = cyy = cyz = czz = 0.0
    for v in verts:
        dx, dy, dz = v.x - cx, v.y - cy, v.z - cz
        cxx += dx * dx; cxy += dx * dy; cxz += dx * dz
        cyy += dy * dy; cyz += dy * dz; czz += dz * dz
    k = 1.0 / len(verts)
    cov = [[cxx * k, cxy * k, cxz * k],
           [cxy * k, cyy * k, cyz * k],
           [cxz * k, cyz * k, czz * k]]
    # Power iteration for top eigenvector — sufficient for "longest axis"
    v = Vector((1.0, 1.0, 1.0)).normalized()
    for _ in range(32):
        nv = Vector((
            cov[0][0] * v.x + cov[0][1] * v.y + cov[0][2] * v.z,
            cov[1][0] * v.x + cov[1][1] * v.y + cov[1][2] * v.z,
            cov[2][0] * v.x + cov[2][1] * v.y + cov[2][2] * v.z,
        ))
        nv_len = nv.length
        if nv_len < 1e-12:
            break
        v = nv * (1.0 / nv_len)
    # Project verts onto v to estimate length
    projs = [v.dot(Vector((vert.x - cx, vert.y - cy, vert.z - cz))) for vert in verts]
    length = (max(projs) - min(projs)) if projs else 0.0
    return v, length


def _bounding_sphere_radius(obj):
    """Local-space bounding sphere radius (after centering, from origin)."""
    me = obj.data
    if not me.vertices:
        return 0.0
    r2 = 0.0
    for vert in me.vertices:
        d2 = vert.co.x * vert.co.x + vert.co.y * vert.co.y + vert.co.z * vert.co.z
        if d2 > r2:
            r2 = d2
    return math.sqrt(r2)


# ---------------------------------------------------------------------------
# Pivot centering
# ---------------------------------------------------------------------------

def _center_pivot_to_bbox(obj):
    """Move object origin to its local bbox center.
    Preserves world position of every vertex.
    Returns the shift vector that was applied to mesh data (local frame)."""
    center_local = _local_bbox_center(obj)
    if center_local.length < 1e-9:
        return Vector((0.0, 0.0, 0.0))  # already centered
    # Shift mesh data so the local center lands on the origin
    obj.data.transform(Matrix.Translation(-center_local))
    # Move the object by the equivalent world delta so geometry stays put
    # world_delta = matrix_world.to_3x3() @ center_local
    world_delta = obj.matrix_world.to_3x3() @ center_local
    obj.matrix_world.translation = obj.matrix_world.translation + world_delta
    # Force a depsgraph update so subsequent reads of matrix_world are fresh
    bpy.context.view_layer.update()
    return center_local


# ---------------------------------------------------------------------------
# Per-part record
# ---------------------------------------------------------------------------

def _quat_wxyz(q):
    return [q.w, q.x, q.y, q.z]


def _mat_rowmajor(m):
    """4x4 mathutils.Matrix → flat 16-float row-major list."""
    return [m[r][c] for r in range(4) for c in range(4)]


def _safe_part_id(name):
    """Filesystem-safe + JSON-key-safe id from a Blender object name."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def _role_lookup_from_overrides(overrides_path):
    """Build {obj_name_normalized -> role_string} from a manual_overrides.json
    file that has the {"pistons": {"piston_1A": "V8 Engine-.281"}, ...} shape.
    Returns {} if file missing or malformed."""
    if not overrides_path or not os.path.exists(overrides_path):
        return {}
    try:
        data = json.loads(Path(overrides_path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    lookup = {}
    for section in ("pistons", "crank_throws", "rods"):
        block = data.get(section, {})
        if not isinstance(block, dict):
            continue
        for role, raw_id in block.items():
            if not isinstance(raw_id, str):
                continue
            # The GLB sanitizes "V8 Engine-.281" → "V8_Engine-281"; record both
            sanitized = raw_id.replace(" ", "_").replace(".", "")
            lookup[raw_id] = role
            lookup[sanitized] = role
    return lookup


def _record_for_object(obj, asset_id, role_hint, neighbors):
    mat = obj.matrix_world.copy()
    loc = mat.translation
    rot_eul = mat.to_euler("XYZ")
    rot_q = mat.to_quaternion()
    scl = mat.to_scale()
    lmn, lmx = _local_bbox(obj)
    lsz = lmx - lmn
    diag = lsz.length
    sphere_r = _bounding_sphere_radius(obj)
    axis_local, axis_len = _principal_axis_local(obj)
    me = obj.data
    return {
        "schema_version": SCHEMA_VERSION,
        "part_id": _safe_part_id(obj.name),
        "object_name": obj.name,
        "asset_id": asset_id,
        "role_hint": role_hint,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rest_transform": {
            "location_world": [loc.x, loc.y, loc.z],
            "rotation_euler_xyz_rad": [rot_eul.x, rot_eul.y, rot_eul.z],
            "rotation_quaternion_wxyz": _quat_wxyz(rot_q),
            "scale": [scl.x, scl.y, scl.z],
            "matrix_world_rest_rowmajor": _mat_rowmajor(mat),
        },
        "geometry_local": {
            "bbox_min": [lmn.x, lmn.y, lmn.z],
            "bbox_max": [lmx.x, lmx.y, lmx.z],
            "bbox_center": [0.0, 0.0, 0.0],
            "bbox_size": [lsz.x, lsz.y, lsz.z],
            "bbox_diagonal": diag,
            "bounding_sphere_radius": sphere_r,
            "principal_axis": [axis_local.x, axis_local.y, axis_local.z],
            "long_axis_length_m": axis_len,
            "vertex_count": len(me.vertices),
            "face_count": len(me.polygons),
        },
        "pivot_history": {},  # filled in by direct_blender (it knows the shift)
        "neighbors": neighbors,
    }


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def direct_blender(asset_id,
                   out_dir,
                   collection=None,
                   name_prefix=None,
                   role_overrides_json=None,
                   neighbor_k=4):
    """Pivot-center every mesh in scope; write per-part rest JSONs + manifest.

    Args:
      asset_id: short id, e.g. "v8_engine". Goes into every JSON.
      out_dir:  absolute path; will be created if missing.
      collection: optional collection name to limit scope.
      name_prefix: optional substring; only object names containing it.
      role_overrides_json: optional path to manual_overrides.json for role_hint.
      neighbor_k: number of nearest neighbors per part (by bbox-center distance).

    Returns: {asset_id, parts_processed, out_dir, manifest_path}.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ------- Pick the meshes in scope -------
    if collection and collection in bpy.data.collections:
        candidates = list(bpy.data.collections[collection].all_objects)
    else:
        candidates = list(bpy.context.scene.objects)
    meshes = [o for o in candidates
              if o.type == "MESH"
              and (not name_prefix or name_prefix in o.name)]
    if not meshes:
        print(f"[blender_director] WARN: no meshes matched scope")
        return {"asset_id": asset_id, "parts_processed": 0, "out_dir": str(out)}

    role_lookup = _role_lookup_from_overrides(role_overrides_json)

    # ------- Snapshot original-origin locations BEFORE we touch anything,
    #         so pivot_history records the truth even if a part is already
    #         centered.
    original_origin_world = {o.name: o.matrix_world.translation.copy() for o in meshes}

    # ------- Centering pass: move every origin to its local bbox center -------
    shifts = {}
    for obj in meshes:
        shifts[obj.name] = _center_pivot_to_bbox(obj)

    # ------- Compute world bbox centers for neighbor lookup (after centering,
    #         the world translation IS the bbox center, by construction) -----
    centers = {o.name: o.matrix_world.translation.copy() for o in meshes}

    def _nearest_k(name, k):
        c = centers[name]
        dists = []
        for other_name, oc in centers.items():
            if other_name == name:
                continue
            d = (oc - c).length
            dists.append((d, other_name, oc))
        dists.sort(key=lambda t: t[0])
        out_list = []
        for d, other_name, oc in dists[:k]:
            direction = (oc - c)
            if direction.length > 1e-9:
                direction = direction.normalized()
            out_list.append({
                "part_id": _safe_part_id(other_name),
                "object_name": other_name,
                "distance_m": d,
                "direction_world": [direction.x, direction.y, direction.z],
            })
        return out_list

    # ------- Per-part JSONs -------
    index_entries = []
    for obj in meshes:
        role_hint = role_lookup.get(obj.name)
        neighbors = _nearest_k(obj.name, neighbor_k)
        rec = _record_for_object(obj, asset_id, role_hint, neighbors)
        rec["pivot_history"] = {
            "method": "bbox_center",
            "origin_world_before": list(original_origin_world[obj.name]),
            "origin_world_after": list(centers[obj.name]),
            "shift_local_applied": list(shifts[obj.name]),
        }
        part_id = rec["part_id"]
        json_path = out / f"{part_id}.rest.json"
        json_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        index_entries.append({
            "part_id": part_id,
            "object_name": obj.name,
            "role_hint": role_hint,
            "rest_json": json_path.name,
            "location_world": rec["rest_transform"]["location_world"],
            "bbox_size": rec["geometry_local"]["bbox_size"],
        })

    # ------- Index + manifest -------
    index = {
        "schema_version": SCHEMA_VERSION,
        "asset_id": asset_id,
        "part_count": len(index_entries),
        "parts": index_entries,
    }
    (out / "_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")

    # Asset-level world bbox (union of all part bboxes)
    abb_mn = Vector((1e9, 1e9, 1e9))
    abb_mx = Vector((-1e9, -1e9, -1e9))
    for obj in meshes:
        mn, mx = _world_bbox(obj)
        for i, k in enumerate(("x", "y", "z")):
            if mn[i] < abb_mn[i]: abb_mn[i] = mn[i]
            if mx[i] > abb_mx[i]: abb_mx[i] = mx[i]
    asset_size = abb_mx - abb_mn
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "asset_id": asset_id,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "blender_version": bpy.app.version_string,
        "script": "spatail_blender_director.py",
        "scope": {
            "collection": collection,
            "name_prefix": name_prefix,
            "meshes_processed": len(meshes),
        },
        "asset_bbox_world": {
            "min": [abb_mn.x, abb_mn.y, abb_mn.z],
            "max": [abb_mx.x, abb_mx.y, abb_mx.z],
            "size": [asset_size.x, asset_size.y, asset_size.z],
            "center": [(abb_mn.x + abb_mx.x) * 0.5,
                       (abb_mn.y + abb_mx.y) * 0.5,
                       (abb_mn.z + abb_mx.z) * 0.5],
        },
        "notes": "Every part's origin is now at its own local bbox center. "
                 "Restore any part by applying its matrix_world_rest_rowmajor.",
    }
    manifest_path = out / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"[blender_director] processed {len(meshes)} meshes → {out}")
    return {
        "asset_id": asset_id,
        "parts_processed": len(meshes),
        "out_dir": str(out),
        "manifest_path": str(manifest_path),
    }


# ---------------------------------------------------------------------------
# Verify API — color-segment + render so the user can confirm overrides
# ---------------------------------------------------------------------------

def verify_overrides(role_overrides_json,
                     out_dir,
                     name_prefix=None,
                     resolution=(1400, 1000)):
    """Color-segment the parts named in `role_overrides_json` and render 3
    orthographic-ish views so a human (or vision model) can confirm the
    roles map to the RIGHT meshes BEFORE running rest extraction.

    Why this exists: the V8 CAD's initial "crank_throws" hand-curation was
    wrong — it picked the cam lobes at the TOP of the engine instead of
    the real crankshaft throws at the BOTTOM. The bug was invisible until
    we color-segmented the candidates and rendered them in context. Bake
    that verification step into the pipeline so the mistake can't happen
    silently again.

    Writes:
      <out_dir>/verify_front.png
      <out_dir>/verify_side.png
      <out_dir>/verify_3q.png

    For each role-tagged part: bright emissive color, distinct per role
    family (pistons → blue band, crank_throws → red band, rods → green
    band). Everything else: semi-transparent gray shell so the part's
    physical location in the engine is visible.

    Returns: {parts_colored, render_paths, missing_objects}.
    """
    import colorsys
    rest_dir = Path(out_dir)
    rest_dir.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(Path(role_overrides_json).read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": f"could not read overrides: {e}"}

    # Snapshot the current materials so we can restore after rendering
    snapshot = {}
    for o in bpy.context.scene.objects:
        if o.type != "MESH": continue
        if name_prefix and name_prefix not in o.name: continue
        snapshot[o.name] = [m.name if m else None for m in o.data.materials]

    # Build / get the shell material
    shell = bpy.data.materials.get("__verify_shell__")
    if shell is None:
        shell = bpy.data.materials.new("__verify_shell__")
        shell.use_nodes = True
    if shell.use_nodes:
        bsdf = shell.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (0.70, 0.72, 0.78, 1.0)
            bsdf.inputs["Alpha"].default_value = 0.25
            bsdf.inputs["Roughness"].default_value = 0.40
    shell.blend_method = "BLEND"

    def hsv(h, s, v):
        r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
        return (r, g, b, 1.0)

    def make_glow(name, color):
        m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        m.use_nodes = True
        bsdf = m.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = color
            bsdf.inputs["Alpha"].default_value = 1.0
            if "Emission Color" in bsdf.inputs:
                bsdf.inputs["Emission Color"].default_value = color
            if "Emission Strength" in bsdf.inputs:
                bsdf.inputs["Emission Strength"].default_value = 10.0
        m.blend_method = "OPAQUE"
        return m

    # Apply shell to every in-scope mesh
    for o in bpy.context.scene.objects:
        if o.type != "MESH": continue
        if name_prefix and name_prefix not in o.name: continue
        o.hide_render = False; o.hide_viewport = False
        o.data.materials.clear()
        o.data.materials.append(shell)

    colored = 0
    missing = []
    # Apply per-role glow colors
    HUE = {"pistons": 0.60, "crank_throws": 0.00, "rods": 0.33}
    for section, hue in HUE.items():
        block = data.get(section, {})
        if not isinstance(block, dict) or block.get("_disabled"):
            continue
        items = [(r, n) for r, n in block.items() if isinstance(n, str)]
        for i, (role, obj_name) in enumerate(items):
            obj = bpy.data.objects.get(obj_name)
            if obj is None:
                missing.append((role, obj_name))
                continue
            shifted_hue = (hue + 0.03 * i) % 1.0
            mat = make_glow(f"__verify_{role}__", hsv(shifted_hue, 0.95, 1.0))
            obj.data.materials.clear()
            obj.data.materials.append(mat)
            colored += 1

    # Camera setup
    cam = bpy.data.objects.get("__verify_cam__")
    if cam is None:
        cam_data = bpy.data.cameras.new("__verify_cam__")
        cam = bpy.data.objects.new("__verify_cam__", cam_data)
        bpy.context.collection.objects.link(cam)
    cam.data.lens = 50

    # Engine center from a quick world-bbox pass
    bb_mn = Vector((1e9,1e9,1e9)); bb_mx = Vector((-1e9,-1e9,-1e9))
    for o in bpy.context.scene.objects:
        if o.type != "MESH": continue
        if name_prefix and name_prefix not in o.name: continue
        mn, mx = _world_bbox(o)
        for i in range(3):
            if mn[i] < bb_mn[i]: bb_mn[i] = mn[i]
            if mx[i] > bb_mx[i]: bb_mx[i] = mx[i]
    target = (bb_mn + bb_mx) * 0.5
    size = bb_mx - bb_mn
    dist = max(size) * 1.4

    bpy.context.scene.camera = cam
    bpy.context.scene.render.resolution_x = resolution[0]
    bpy.context.scene.render.resolution_y = resolution[1]
    bpy.context.scene.render.image_settings.file_format = "PNG"
    bpy.context.scene.world.color = (0.06, 0.07, 0.10)
    try:
        bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        try: bpy.context.scene.render.engine = "BLENDER_EEVEE"
        except Exception: pass

    def look_at(o, t):
        d = Vector(t) - o.location
        o.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()

    shots = [
        ("verify_front", target + Vector(( 0.0, -dist * 0.7, 0.0))),
        ("verify_side",  target + Vector(( dist * 0.7, 0.0, 0.0))),
        ("verify_3q",    target + Vector(( dist * 0.55, -dist * 0.55, dist * 0.35))),
    ]
    render_paths = []
    for name, pos in shots:
        cam.location = pos
        look_at(cam, target)
        bpy.context.view_layer.update()
        path = rest_dir / f"{name}.png"
        bpy.context.scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        render_paths.append(str(path))

    print(f"[verify] colored {colored} parts, rendered {len(render_paths)} views → {rest_dir}")
    return {
        "parts_colored": colored,
        "missing_objects": missing,
        "render_paths": render_paths,
        "note": "Inspect the renders. If a role-colored part is in the WRONG physical location, "
                "fix manual_overrides.json BEFORE running direct_blender(). Animation will use "
                "exactly the parts you tag here.",
    }


# ---------------------------------------------------------------------------
# Restore API
# ---------------------------------------------------------------------------

def restore_from_rest(rest_dir):
    """Read every *.rest.json in `rest_dir` and re-apply the recorded
    matrix_world_rest_rowmajor to the matching scene object (by name).

    Useful after experimentation has shifted parts. Per-part: does not
    touch parents, constraints, modifiers, or geometry — only the world
    transform.

    Returns: {restored: int, missing: [object_name, ...]}.
    """
    rest_dir = Path(rest_dir)
    files = sorted(rest_dir.glob("*.rest.json"))
    restored = 0
    missing = []
    for f in files:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[restore] skip {f.name}: {e}")
            continue
        obj_name = rec.get("object_name") or rec.get("part_id")
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            missing.append(obj_name)
            continue
        flat = rec.get("rest_transform", {}).get("matrix_world_rest_rowmajor")
        if not flat or len(flat) != 16:
            missing.append(obj_name)
            continue
        m = Matrix((flat[0:4], flat[4:8], flat[8:12], flat[12:16]))
        obj.matrix_world = m
        restored += 1
    bpy.context.view_layer.update()
    print(f"[blender_director] restored {restored}/{len(files)} (missing {len(missing)})")
    return {"restored": restored, "missing": missing}


# ---------------------------------------------------------------------------
# When executed directly via `exec(open(...).read())`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Convenience self-run for quick interactive use. Adjust as needed.
    direct_blender(
        asset_id="current_scene",
        out_dir=r"C:/SPATAIL_MAX/assets_processed/rest_poses/current_scene",
        role_overrides_json=r"C:/SPATAIL_MAX/engineexplainer/engine/manual_overrides.json",
    )
