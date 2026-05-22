"""
spatail_classify_engine.py — translate a treatment manifest into engine roles.

See skills/spatail-classify-engine/SKILL.md for the design rationale.

INPUT:  <asset>.treatment.json from spatail_treat_mesh
OUTPUT: <asset>.classification.json with role/cyl/bank/throwId per part
        and kinematicGroups linking (piston, rod, pin, throw) per cylinder.

This module is a PURE FUNCTION of the manifest JSON. It does not touch
Blender. You can run it outside Blender, in CI, or against frozen
manifests for regression testing.
"""

import json, math, os
from collections import defaultdict
from datetime import datetime, timezone


def _vec(a):
    return list(a) if a else [0, 0, 0]


def _dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def _vec_sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _vec_add(a, b):
    return [a[i] + b[i] for i in range(3)]


def _vec_dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _vec_norm(a):
    m = math.sqrt(sum(x * x for x in a))
    return [x / m for x in a] if m > 1e-9 else [0, 0, 0]


def _project_onto(v, axis):
    """Scalar projection of v onto axis (assumed unit vector)."""
    return _vec_dot(v, axis)


def _perp_to(v, axis):
    """Component of v perpendicular to axis."""
    s = _vec_dot(v, axis)
    return [v[i] - s * axis[i] for i in range(3)]


def _bbox_size(part_pca):
    """Reconstruct rough size from eigvals (proxy for principal extent).
    Each eigenvalue = variance along that axis; sqrt(12 * var) ≈ extent
    for uniform sampling. We just need a relative ordering."""
    eig = part_pca.get("eigvals", [])
    if not eig: return 0.0
    return math.sqrt(eig[0] * 12.0)


def _kmeans_1d(values, k=2, max_iter=20):
    """Tiny 1-D k-means. Returns (centroids, labels)."""
    if not values: return [], []
    vmin, vmax = min(values), max(values)
    if vmin == vmax: return [vmin], [0] * len(values)
    centroids = [vmin + (vmax - vmin) * i / max(1, k - 1) for i in range(k)]
    for _ in range(max_iter):
        labels = [min(range(k), key=lambda c: abs(v - centroids[c])) for v in values]
        new_cent = []
        for c in range(k):
            members = [values[i] for i, lbl in enumerate(labels) if lbl == c]
            new_cent.append(sum(members) / len(members) if members else centroids[c])
        if all(abs(new_cent[c] - centroids[c]) < 1e-6 for c in range(k)):
            break
        centroids = new_cent
    return centroids, labels


def _pca_1d_axis(points):
    """Fit a line to a cluster of 3D points; return (origin, axis_unit).
    Uses pure-python power iteration on the 3x3 covariance."""
    n = len(points)
    if n < 2: return points[0] if points else [0, 0, 0], [0, 0, 1]
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    cz = sum(p[2] for p in points) / n
    M = [[0.0] * 3 for _ in range(3)]
    for p in points:
        dx, dy, dz = p[0] - cx, p[1] - cy, p[2] - cz
        M[0][0] += dx * dx; M[0][1] += dx * dy; M[0][2] += dx * dz
        M[1][1] += dy * dy; M[1][2] += dy * dz
        M[2][2] += dz * dz
    M[1][0] = M[0][1]; M[2][0] = M[0][2]; M[2][1] = M[1][2]
    for i in range(3):
        for j in range(3):
            M[i][j] /= n
    v = [1.0, 1.0, 1.0]
    for _ in range(80):
        nv = [sum(M[i][j] * v[j] for j in range(3)) for i in range(3)]
        mag = math.sqrt(sum(x * x for x in nv)) or 1.0
        v = [x / mag for x in nv]
    return [cx, cy, cz], v


# ============================================================================
# Classifier
# ============================================================================

def classify_engine(treatment_json, out_json=None):
    with open(treatment_json) as f:
        M = json.load(f)
    parts = M["stages"]["4_principal_geometry"]["parts"]
    pivots = {p["name"]: p for p in M["stages"]["5_pivots"]["parts"]}
    topology = {o["name"]: o for o in M["stages"]["2_topology"]["objects"]}
    # Refresh topology to reflect post-segmentation parts (segmented parts
    # appear in stages 4-6 but not necessarily 2 if topology ran pre-split).
    # We get bbox info from stage 4 / pivots / hand-compute as needed.

    # ---- Step 1: bucket by shape class ---------------------------------
    blobs = [p for p in parts if p["shape_class"] == "blob"]
    discs = [p for p in parts if p["shape_class"] == "disc-like"]
    rods  = [p for p in parts if p["shape_class"] == "rod-like"]

    # ---- Step 2: find the crank axis from blob centroids ---------------
    blob_centres = [p["bbox_centre"] for p in blobs]
    crank_origin, crank_axis = _pca_1d_axis(blob_centres)
    # Verify the throws really lie along a line: residual perp to axis
    perp_residuals = []
    for c in blob_centres:
        d = _vec_sub(c, crank_origin)
        perp = _perp_to(d, crank_axis)
        perp_residuals.append(math.sqrt(sum(x * x for x in perp)))
    avg_perp = sum(perp_residuals) / max(1, len(perp_residuals))

    # Sort throws by projection along crank axis (= cylinder index)
    throws_with_proj = []
    for p in blobs:
        proj = _vec_dot(_vec_sub(p["bbox_centre"], crank_origin), crank_axis)
        throws_with_proj.append((proj, p))
    throws_with_proj.sort(key=lambda x: -x[0])  # highest projection = cyl 1
    throw_assignments = {}
    for i, (proj, p) in enumerate(throws_with_proj):
        throw_assignments[p["name"]] = {
            "role": "crank_throw",
            "cylinderIndex": i + 1,
            "throwId": f"throw_{i+1}",
            "projection_along_crank": round(proj, 4),
            "journalCentre": pivots[p["name"]]["pivot_world"]
                              if p["name"] in pivots else p["bbox_centre"],
        }

    # ---- Step 3: identify pistons (discs perpendicular to crank) -------
    # Cluster pistons by projection along crank axis → cyl assignment
    piston_records = []
    for p in discs:
        c = p["bbox_centre"]
        proj = _vec_dot(_vec_sub(c, crank_origin), crank_axis)
        # Bank: signed perp distance from crank axis. We need a stable
        # "bank axis" perpendicular to crank. Use world-X projected
        # perp to crank as the bank reference.
        world_x = [1.0, 0.0, 0.0]
        bank_axis = _vec_norm(_perp_to(world_x, crank_axis))
        d = _vec_sub(c, crank_origin)
        bank_proj = _vec_dot(d, bank_axis)
        piston_records.append({"name": p["name"], "centre": c, "proj_along": proj,
                                "bank_proj": bank_proj, "raw": p})

    # Find cylinder positions (Z-clusters of pistons should match throws)
    # Cluster piston projections into N groups where N = throw count
    n_cyl = len(throws_with_proj)
    # Sort pistons by projection, cluster into n_cyl bins by proximity
    piston_records.sort(key=lambda r: -r["proj_along"])
    piston_assignments = {}
    if piston_records and n_cyl:
        # Pair pistons to throws by closest projection
        for pr in piston_records:
            best = min(throws_with_proj, key=lambda tp: abs(tp[0] - pr["proj_along"]))
            cyl_idx = throw_assignments[best[1]["name"]]["cylinderIndex"]
            pr["cylinderIndex"] = cyl_idx
        # Within each cyl: split by bank_proj sign
        from collections import defaultdict
        by_cyl = defaultdict(list)
        for pr in piston_records:
            by_cyl[pr["cylinderIndex"]].append(pr)
        for cyl_idx, members in by_cyl.items():
            # Assign higher-bank_proj to B, lower to A
            members.sort(key=lambda r: r["bank_proj"])
            for i, pr in enumerate(members):
                bank = "A" if i == 0 else "B"
                piston_assignments[pr["name"]] = {
                    "role": "piston",
                    "cylinderIndex": cyl_idx,
                    "bank": bank,
                    "centre": pr["centre"],
                    "bank_proj": round(pr["bank_proj"], 4),
                }

    # ---- Step 4: split rod-like parts into conrods vs wrist pins ------
    # Length proxy: sqrt(12 * primary_eigenvalue) ≈ extent along principal axis
    rod_lengths = [_bbox_size(p) for p in rods]
    rod_classifications = {}
    if len(rod_lengths) >= 2:
        centres, labels = _kmeans_1d(rod_lengths, k=2)
        # Whichever cluster has larger centre = conrods
        cluster_for_conrod = 0 if centres[0] > centres[1] else 1
        for p, lbl, ln in zip(rods, labels, rod_lengths):
            role = "connecting_rod" if lbl == cluster_for_conrod else "wrist_pin"
            rod_classifications[p["name"]] = {"role": role, "length_cm": round(ln, 4),
                                                "centre": p["bbox_centre"],
                                                "principal_axis": p["principal_axis"]}
    else:
        # Only 0 or 1 rod-like — call them all conrods
        for p in rods:
            rod_classifications[p["name"]] = {"role": "connecting_rod",
                                                "length_cm": round(_bbox_size(p), 4),
                                                "centre": p["bbox_centre"],
                                                "principal_axis": p["principal_axis"]}

    # ---- Step 5: pair into kinematic groups ----------------------------
    # For each (cyl, bank) piston, find nearest conrod + nearest wrist pin
    # whose endpoints are consistent with connecting that piston to its throw.
    cyl_to_throw = {throw_assignments[n]["cylinderIndex"]: throw_assignments[n]
                    for n in throw_assignments}
    pistons_by_cyl_bank = {}
    for n, info in piston_assignments.items():
        pistons_by_cyl_bank[(info["cylinderIndex"], info["bank"])] = (n, info)

    used_conrods = set()
    used_pins = set()
    kinematic_groups = []
    for (cyl, bank), (pname, pinfo) in sorted(pistons_by_cyl_bank.items()):
        piston_c = pinfo["centre"]
        throw_info = cyl_to_throw.get(cyl)
        journal = throw_info["journalCentre"] if throw_info else piston_c
        # Find conrod best matching (one endpoint near piston, other near journal)
        best_rod = None; best_rod_score = float("inf")
        for rname, ri in rod_classifications.items():
            if ri["role"] != "connecting_rod" or rname in used_conrods: continue
            d_p = _dist(ri["centre"], piston_c)
            d_j = _dist(ri["centre"], journal)
            # Score: closer to BOTH endpoints simultaneously is better
            score = d_p + d_j
            if score < best_rod_score:
                best_rod_score = score; best_rod = rname
        if best_rod: used_conrods.add(best_rod)
        # Find wrist pin nearest piston centroid
        best_pin = None; best_pin_d = float("inf")
        for rname, ri in rod_classifications.items():
            if ri["role"] != "wrist_pin" or rname in used_pins: continue
            d_p = _dist(ri["centre"], piston_c)
            if d_p < best_pin_d:
                best_pin_d = d_p; best_pin = rname
        if best_pin: used_pins.add(best_pin)

        kinematic_groups.append({
            "id": f"cyl_{cyl}_{bank}",
            "cylinderIndex": cyl,
            "bank": bank,
            "piston": pname,
            "connectingRod": best_rod,
            "wristPin": best_pin,
            "throw": throw_info["throwId"] if throw_info else None,
        })

    # Now decorate rod + pin assignments with their cyl/bank from groups
    rod_to_cyl = {}
    pin_to_cyl = {}
    for g in kinematic_groups:
        if g["connectingRod"]: rod_to_cyl[g["connectingRod"]] = (g["cylinderIndex"], g["bank"])
        if g["wristPin"]:      pin_to_cyl[g["wristPin"]] = (g["cylinderIndex"], g["bank"])

    # ---- Build flat parts list -----------------------------------------
    classified_parts = []
    for p in parts:
        n = p["name"]
        if n in throw_assignments:
            classified_parts.append({"name": n, **throw_assignments[n]})
        elif n in piston_assignments:
            classified_parts.append({"name": n, **piston_assignments[n]})
        elif n in rod_classifications:
            ri = rod_classifications[n]
            entry = {"name": n, **ri}
            cyl_bank = rod_to_cyl.get(n) or pin_to_cyl.get(n)
            if cyl_bank:
                entry["cylinderIndex"] = cyl_bank[0]
                entry["bank"] = cyl_bank[1]
            classified_parts.append(entry)
        else:
            classified_parts.append({"name": n, "role": "unclassified",
                                       "shape_class": p["shape_class"]})

    # ---- Summary --------------------------------------------------------
    n_throws  = sum(1 for c in classified_parts if c.get("role") == "crank_throw")
    n_pistons = sum(1 for c in classified_parts if c.get("role") == "piston")
    n_rods    = sum(1 for c in classified_parts if c.get("role") == "connecting_rod")
    n_pins    = sum(1 for c in classified_parts if c.get("role") == "wrist_pin")
    complete  = sum(1 for g in kinematic_groups
                    if g["piston"] and g["connectingRod"] and g["throw"])
    missing_pin = sum(1 for g in kinematic_groups
                      if g["piston"] and g["connectingRod"] and g["throw"]
                      and not g["wristPin"])

    out = {
        "assetId": M.get("assetId"),
        "schemaVersion": "0.1.0-spatail-classify-engine",
        "classifiedAt": datetime.now(timezone.utc).isoformat(),
        "treatmentSource": treatment_json,
        "crank": {
            "axis_direction": [round(c, 4) for c in crank_axis],
            "axis_origin":    [round(c, 4) for c in crank_origin],
            "throw_count":    len(throws_with_proj),
            "throw_perp_residual_avg_cm": round(avg_perp, 4),
        },
        "parts": classified_parts,
        "kinematicGroups": kinematic_groups,
        "summary": {
            "throwCount":  n_throws,
            "pistonCount": n_pistons,
            "rodCount":    n_rods,
            "pinCount":    n_pins,
            "cylinderGroupsTotal":      len(kinematic_groups),
            "cylinderGroupsComplete":   complete,
            "cylinderGroupsMissingPin": missing_pin,
        },
    }

    if out_json:
        os.makedirs(os.path.dirname(out_json), exist_ok=True)
        with open(out_json, "w") as f:
            json.dump(out, f, indent=2)
        print(f"[classify_engine] -> {out_json}")
    return out


print("[spatail_classify_engine] module loaded.")
