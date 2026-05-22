"""
spatail_cluster_parts.py — DBSCAN spatial clustering on treated parts.

For high-detail CADs (thousands of mesh islands), this groups parts that
are spatially proximate into functional clusters. Output: cluster
assignments + per-cluster summary JSON.

Pure-python DBSCAN — no scipy / sklearn dependency.
"""
import json, math, os
from datetime import datetime, timezone


def _euclid(a, b):
    dx = a[0] - b[0]; dy = a[1] - b[1]; dz = a[2] - b[2]
    return math.sqrt(dx*dx + dy*dy + dz*dz)


def _region_query(points, idx, eps, grid):
    """Find indices of all points within eps of points[idx].
    Uses a coarse spatial grid for O(N) typical-case lookup."""
    p = points[idx]
    # Find grid cells overlapping eps-ball around p
    cell_size = grid["cell_size"]
    cx = int(math.floor(p[0] / cell_size))
    cy = int(math.floor(p[1] / cell_size))
    cz = int(math.floor(p[2] / cell_size))
    r = int(math.ceil(eps / cell_size)) + 1
    out = []
    cells = grid["cells"]
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            for dz in range(-r, r + 1):
                key = (cx + dx, cy + dy, cz + dz)
                if key not in cells: continue
                for j in cells[key]:
                    if j == idx: continue
                    if _euclid(points[idx], points[j]) <= eps:
                        out.append(j)
    return out


def _build_grid(points, cell_size):
    cells = {}
    for i, p in enumerate(points):
        key = (int(math.floor(p[0] / cell_size)),
               int(math.floor(p[1] / cell_size)),
               int(math.floor(p[2] / cell_size)))
        cells.setdefault(key, []).append(i)
    return {"cell_size": cell_size, "cells": cells}


def dbscan(points, eps, min_samples):
    """Pure-python DBSCAN. Returns list of cluster ids (-1 for noise)."""
    n = len(points)
    labels = [-1] * n  # -1 = unvisited / noise
    visited = [False] * n
    next_cluster_id = 0
    grid = _build_grid(points, eps)

    for i in range(n):
        if visited[i]: continue
        visited[i] = True
        neighbors = _region_query(points, i, eps, grid)
        if len(neighbors) + 1 < min_samples:
            labels[i] = -1  # noise (may be relabelled below)
            continue
        # Expand cluster
        cluster_id = next_cluster_id
        next_cluster_id += 1
        labels[i] = cluster_id
        seeds = list(neighbors)
        idx = 0
        while idx < len(seeds):
            q = seeds[idx]; idx += 1
            if not visited[q]:
                visited[q] = True
                q_neighbors = _region_query(points, q, eps, grid)
                if len(q_neighbors) + 1 >= min_samples:
                    for nb in q_neighbors:
                        if nb not in seeds: seeds.append(nb)
            if labels[q] == -1:
                labels[q] = cluster_id
    return labels


def cluster_treatment(treatment_json_path, out_json=None,
                       eps_fraction_of_diag=0.02, min_samples=2,
                       singletons_as_clusters=True):
    """Run DBSCAN spatial clustering on a treatment manifest's parts.

    Returns a cluster manifest dict.
    """
    with open(treatment_json_path) as f:
        T = json.load(f)
    asset_id = T.get("assetId", "asset")
    parts = T["stages"]["4_principal_geometry"]["parts"]
    topology = T["stages"]["2_topology"]
    bbox_size = topology["asset_bbox_size"]
    diag = math.sqrt(bbox_size[0]**2 + bbox_size[1]**2 + bbox_size[2]**2)
    eps = diag * eps_fraction_of_diag

    centroids = [p["bbox_centre"] for p in parts]
    labels = dbscan(centroids, eps, min_samples)

    # Build clusters
    clusters_by_id = {}
    for i, lbl in enumerate(labels):
        if lbl == -1:
            if singletons_as_clusters:
                # promote each noise point to its own cluster
                lbl = f"singleton_{i}"
            else:
                continue
        clusters_by_id.setdefault(lbl, []).append(i)

    cluster_list = []
    for cid, members in clusters_by_id.items():
        # Compute union bbox + shape mix
        lo = [float("inf"), float("inf"), float("inf")]
        hi = [-float("inf"), -float("inf"), -float("inf")]
        shape_mix = {}
        member_part_ids = []
        for m in members:
            p = parts[m]
            member_part_ids.append(p["name"])
            shape_mix[p["shape_class"]] = shape_mix.get(p["shape_class"], 0) + 1
            # Centroid bbox approximation: ±0 (we don't have per-part bbox stored
            # in stage 4; would need stage 2 lookup). For now use the centroid.
            c = p["bbox_centre"]
            for ax in range(3):
                lo[ax] = min(lo[ax], c[ax])
                hi[ax] = max(hi[ax], c[ax])
        centroid = [(lo[ax] + hi[ax]) * 0.5 for ax in range(3)]
        # Candidate hint based on shape_mix
        if len(members) >= 3 and shape_mix.get("disc-like", 0) >= 2:
            hint = "button_or_dial_assembly"
        elif len(members) >= 4 and shape_mix.get("rod-like", 0) >= 3:
            hint = "frame_or_bracket_assembly"
        elif len(members) == 1:
            hint = "singleton_part"
        else:
            hint = "mixed_assembly"
        cluster_list.append({
            "cluster_id": cid if isinstance(cid, int) else int(cid.split("_")[1]) + 100000,
            "cluster_label": str(cid),
            "member_count": len(members),
            "member_part_ids": member_part_ids,
            "shape_mix": shape_mix,
            "bbox_lo": [round(c, 3) for c in lo],
            "bbox_hi": [round(c, 3) for c in hi],
            "centroid_world": [round(c, 3) for c in centroid],
            "candidate_hint": hint,
        })
    # Sort clusters by member count descending
    cluster_list.sort(key=lambda c: -c["member_count"])

    n_total_clusters = len(cluster_list)
    n_real_clusters = sum(1 for c in cluster_list if c["member_count"] >= min_samples)
    n_singletons = sum(1 for c in cluster_list if c["member_count"] == 1)

    out = {
        "assetId": asset_id,
        "schemaVersion": "0.1.0-cluster-parts",
        "clusteredAt": datetime.now(timezone.utc).isoformat(),
        "params": {
            "eps_cm": round(eps, 3),
            "eps_fraction_of_diag": eps_fraction_of_diag,
            "min_samples": min_samples,
            "asset_diag_cm": round(diag, 3),
        },
        "summary": {
            "input_parts": len(parts),
            "total_clusters": n_total_clusters,
            "real_clusters_ge_min_samples": n_real_clusters,
            "singletons": n_singletons,
            "largest_cluster_size": cluster_list[0]["member_count"] if cluster_list else 0,
            "candidate_hint_histogram": {},
        },
        "clusters": cluster_list,
    }
    # Hint histogram
    for c in cluster_list:
        h = c["candidate_hint"]
        out["summary"]["candidate_hint_histogram"][h] = \
            out["summary"]["candidate_hint_histogram"].get(h, 0) + 1

    if out_json:
        os.makedirs(os.path.dirname(out_json), exist_ok=True)
        with open(out_json, "w") as f:
            json.dump(out, f, indent=2)
        print(f"[cluster_parts] -> {out_json}")
    return out


print("[spatail_cluster_parts] module loaded.")
