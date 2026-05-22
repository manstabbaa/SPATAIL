"""
spatail_measure_per_part.py — per-part isolated measurement with vertex
group painting.

For each part in the asset:
  1. Note its role from classification.json (crank_throw / piston / rod / pin)
  2. Run shape-specific cylinder fits + dimension extraction
  3. Paint vertex groups identifying each feature
  4. Write a measurements JSON for that part (variable schema by shape class)
  5. Update an index JSON listing every part's measurement file

Output: assets_processed/measurements/<asset>/<part>.measurements.json
"""
import bpy, json, math, os
from datetime import datetime, timezone
from mathutils import Vector, Matrix


def _ensure_vertex_group(obj, name):
    vg = obj.vertex_groups.get(name)
    if vg is None:
        vg = obj.vertex_groups.new(name=name)
    return vg


def _paint_group(obj, name, vertex_indices, weight=1.0):
    """Create/clear a vertex group and assign these indices."""
    vg = _ensure_vertex_group(obj, name)
    # Remove all first (idempotent re-paint)
    vg.remove([v.index for v in obj.data.vertices])
    vg.add(list(vertex_indices), weight, "REPLACE")


def _set_origin_local(obj, target_local):
    """Shift mesh data so the object origin lands at `target_local`
    (in the mesh's CURRENT local frame). World position preserved."""
    obj.data.transform(Matrix.Translation(-Vector(target_local)))
    obj.matrix_world.translation = obj.matrix_world @ Vector(target_local)


def measure_crank_throw(obj, crank_axis_world_xy):
    """Run journal cylinder fit on a crank throw. Paint journal vertex group.

    Returns measurements dict.
    """
    from spatail_cylinder_fit import ransac_circle_2d  # noqa
    pts_world = [obj.matrix_world @ v.co for v in obj.data.vertices]
    pts_xy = [(p.x, p.y) for p in pts_world]
    cax, cay = crank_axis_world_xy

    fit = ransac_circle_2d(pts_xy, max_iter=800, target_radius=0.9,
                            radius_band=0.6, inlier_tol_cm=0.15, seed=42)
    journal_record = None
    if fit is not None and fit["residual_cm"] < 0.08:
        cx, cy = fit["center_xy"]
        # Inlier vertex indices = those whose XY is within tol of the circle
        inlier_idxs = [i for i, (x, y) in enumerate(pts_xy)
                       if abs(math.hypot(x - cx, y - cy) - fit["radius"]) < 0.15]
        # Median Z of inliers
        inlier_zs = sorted(pts_world[i].z for i in inlier_idxs)
        z = inlier_zs[len(inlier_zs) // 2] if inlier_zs else 0
        world_centre = Vector((cx, cy, z))
        # Convert to local
        local_centre = obj.matrix_world.inverted() @ world_centre
        journal_record = {
            "center_local": [round(c, 4) for c in local_centre],
            "center_world": [round(c, 4) for c in world_centre],
            "radius_cm": round(fit["radius"], 4),
            "throw_radius_cm": round(math.hypot(cx - cax, cy - cay), 4),
            "fit_residual_mm": round(fit["residual_cm"] * 10, 4),
            "n_inliers": fit["n_inliers"],
        }
        # Paint vertex group "spatail.journal"
        _paint_group(obj, "spatail.journal", inlier_idxs)
        # Also paint everything else as "spatail.throw_other"
        other_idxs = [i for i in range(len(pts_world)) if i not in set(inlier_idxs)]
        _paint_group(obj, "spatail.throw_other", other_idxs)

    return {
        "shape_class": "blob",
        "role": "crank_throw",
        "crank_axis_local": [0.0, 0.0, 1.0],  # crank Z in this asset's frame
        "journal": journal_record,
        "pivot": "journal.center_local" if journal_record else "bbox_centre",
        "n_vertices": len(pts_world),
    }


def measure_conrod(obj):
    """Cylinder-fit small + big rings on a conrod. Paint ring vertex groups.

    Determines joint axis (small → big direction) in local frame, c2c length.
    """
    from spatail_cylinder_fit import fit_ring_on_rod_end, detect_long_axis_world  # noqa

    long_axis_world, extent = detect_long_axis_world(obj)
    small_fit = fit_ring_on_rod_end(obj, long_axis_world, end="small",
                                      target_radius=0.5, radius_band=0.5)
    big_fit = fit_ring_on_rod_end(obj, long_axis_world, end="big",
                                    target_radius=0.9, radius_band=0.7)
    rings = {}
    c2c = None
    joint_axis_local = None
    pts_world = [obj.matrix_world @ v.co for v in obj.data.vertices]
    inv = obj.matrix_world.inverted()
    if small_fit and big_fit:
        # By radius, swap if reversed
        if small_fit["radius"] > big_fit["radius"]:
            small_fit, big_fit = big_fit, small_fit
        c2c = (big_fit["center_world"] - small_fit["center_world"]).length
        small_local = inv @ small_fit["center_world"]
        big_local = inv @ big_fit["center_world"]
        joint_dir_local = (big_local - small_local).normalized() if c2c > 1e-3 else None
        rings = {
            "small": {
                "center_local": [round(c, 4) for c in small_local],
                "center_world": [round(c, 4) for c in small_fit["center_world"]],
                "radius_cm": round(small_fit["radius"], 4),
                "fit_residual_mm": round(small_fit["residual_cm"] * 10, 4),
            },
            "big": {
                "center_local": [round(c, 4) for c in big_local],
                "center_world": [round(c, 4) for c in big_fit["center_world"]],
                "radius_cm": round(big_fit["radius"], 4),
                "fit_residual_mm": round(big_fit["residual_cm"] * 10, 4),
            },
        }
        joint_axis_local = [round(c, 4) for c in joint_dir_local] if joint_dir_local else None

        # Paint vertex groups: small ring, big ring
        for end, fit_data in (("small", small_fit), ("big", big_fit)):
            cw = fit_data["center_world"]
            r = fit_data["radius"]
            # Vertices within (0.5r, 1.6r) of the ring centre (a thin shell band)
            idxs = [i for i, p in enumerate(pts_world)
                    if 0.5 * r < (p - cw).length < 1.6 * r]
            _paint_group(obj, f"spatail.{end}_ring", idxs)

    return {
        "shape_class": "rod-like",
        "role": "connecting_rod",
        "joint_rings": rings,
        "joint_axis_local": joint_axis_local,
        "c2c_length_cm": round(c2c, 4) if c2c else None,
        "long_axis_local_extent_cm": round(extent, 4),
        "pivot": "joint_rings.small.center_local",
        "n_vertices": len(pts_world),
    }


def measure_piston(obj):
    """Measure a piston: bore axis (= local long axis), wrist-pin location."""
    from spatail_cylinder_fit import detect_long_axis_world  # noqa

    long_axis_world, extent = detect_long_axis_world(obj)
    # Local long axis index
    lo = Vector((float("inf"),) * 3); hi = Vector((-float("inf"),) * 3)
    for v in obj.data.vertices:
        lo = Vector(map(min, lo, v.co)); hi = Vector(map(max, hi, v.co))
    extents = hi - lo
    long_idx = max(range(3), key=lambda i: extents[i])
    bore_axis_local = [0.0, 0.0, 0.0]
    bore_axis_local[long_idx] = 1.0

    centroid_local = (lo + hi) * 0.5
    # Wrist pin: piston's geometric centre is a reasonable proxy for the
    # wrist-pin axis (perpendicular to bore_axis, through the centre).
    return {
        "shape_class": "disc-like",
        "role": "piston",
        "bore_axis_local": bore_axis_local,
        "bore_extent_cm": round(extents[long_idx], 4),
        "wrist_pin": {
            "center_local": [round(c, 4) for c in centroid_local],
            "axis_local": [1.0, 0.0, 0.0] if long_idx != 0 else [0.0, 0.0, 1.0],
        },
        "pivot": "wrist_pin.center_local",
        "bbox_size_cm": [round(c, 4) for c in extents],
        "n_vertices": len(obj.data.vertices),
    }


def measure_pin(obj):
    """Measure a wrist pin: long axis + radius."""
    lo = Vector((float("inf"),) * 3); hi = Vector((-float("inf"),) * 3)
    for v in obj.data.vertices:
        lo = Vector(map(min, lo, v.co)); hi = Vector(map(max, hi, v.co))
    extents = hi - lo
    long_idx = max(range(3), key=lambda i: extents[i])
    long_axis_local = [0.0, 0.0, 0.0]; long_axis_local[long_idx] = 1.0
    return {
        "shape_class": "rod-like",
        "role": "wrist_pin",
        "long_axis_local": long_axis_local,
        "length_cm": round(extents[long_idx], 4),
        "approx_radius_cm": round(max(extents[i] for i in range(3) if i != long_idx) / 2, 4),
        "pivot": "bbox_centre",
        "n_vertices": len(obj.data.vertices),
    }


def measure_unclassified(obj):
    lo = Vector((float("inf"),) * 3); hi = Vector((-float("inf"),) * 3)
    for v in obj.data.vertices:
        lo = Vector(map(min, lo, v.co)); hi = Vector(map(max, hi, v.co))
    return {
        "shape_class": "unknown",
        "role": "unclassified",
        "bbox_local_lo": [round(c, 4) for c in lo],
        "bbox_local_hi": [round(c, 4) for c in hi],
        "pivot": "bbox_centre",
        "n_vertices": len(obj.data.vertices),
    }


def measure_all_parts(treatment_json, classification_json, out_dir):
    """Run per-part measurement across the asset. Writes one JSON per part."""
    import sys
    sys.path.insert(0, r"C:/SPATAIL_MAX/pipeline/blender")

    with open(treatment_json) as f:
        T = json.load(f)
    with open(classification_json) as f:
        C = json.load(f)

    asset_id = T.get("assetId", "asset")
    os.makedirs(out_dir, exist_ok=True)

    # Build role lookup from classification
    role_by_name = {}
    cyl_bank_by_name = {}
    for part in C.get("parts", []):
        role_by_name[part["name"]] = part.get("role", "unclassified")
        if "cylinderIndex" in part: cyl_bank_by_name[part["name"]] = (
            part.get("cylinderIndex"), part.get("bank"))

    # Crank axis (for journal radius reference)
    crank_axis_origin = C.get("crank", {}).get("axis_origin", [0, 0, 0])
    crank_axis_xy = (crank_axis_origin[0], crank_axis_origin[1])

    index = {
        "assetId": asset_id,
        "schemaVersion": "0.1.0-measure-per-part",
        "measuredAt": datetime.now(timezone.utc).isoformat(),
        "parts": [],
    }

    for part in C.get("parts", []):
        name = part["name"]
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "MESH": continue
        role = role_by_name.get(name, "unclassified")

        if role == "crank_throw":
            measurement = measure_crank_throw(obj, crank_axis_xy)
        elif role == "connecting_rod":
            measurement = measure_conrod(obj)
        elif role == "piston":
            measurement = measure_piston(obj)
        elif role == "wrist_pin":
            measurement = measure_pin(obj)
        else:
            measurement = measure_unclassified(obj)

        cyl, bank = cyl_bank_by_name.get(name, (None, None))
        measurement["partId"] = name
        measurement["role"] = role
        if cyl is not None: measurement["cylinderIndex"] = cyl
        if bank: measurement["bank"] = bank
        measurement["schemaVersion"] = "0.1.0-measure-per-part"
        measurement["measuredAt"] = datetime.now(timezone.utc).isoformat()

        # Sanitise filename
        safe_name = name.replace(":", "_").replace(" ", "_").replace("/", "_")
        json_path = os.path.join(out_dir, f"{safe_name}.measurements.json").replace("\\", "/")
        with open(json_path, "w") as f:
            json.dump(measurement, f, indent=2)
        index["parts"].append({
            "partId": name, "role": role, "cyl": cyl, "bank": bank,
            "json": json_path,
            "shape_class": measurement.get("shape_class"),
        })

    index_path = os.path.join(out_dir, "_index.json").replace("\\", "/")
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)
    print(f"[measure_per_part] {len(index['parts'])} parts measured → {out_dir}")
    return {"index": index_path, "parts": len(index["parts"])}


print("[spatail_measure_per_part] module loaded.")
