"""
spatail_merge_clusters.py — group + merge mesh islands into real-life objects.

Two-pass clustering:
  1. Tight DBSCAN (~1cm in cm units, ~0.5% of asset diagonal) groups
     sub-meshes of the same physical part (button cap + ring + LED).
  2. Bbox-containment merge: small clusters whose bbox is fully inside a
     larger cluster's bbox are merged into the larger cluster (nested-part
     case — e.g., a screw inside a housing).

Then for each cluster with ≥2 members, the meshes are JOINED into one
Blender object. Result: the scene has ~real-life-object count of meshes
instead of thousands of fragments.

USAGE:
    exec(open(r".../spatail_merge_clusters.py").read())
    merge_clusters_in_scene(eps_cm=1.0)
"""
import bpy, math
from mathutils import Vector


def _build_grid(centroids, cell_size):
    grid = {}
    for i, p in enumerate(centroids):
        key = (int(math.floor(p[0] / cell_size)),
               int(math.floor(p[1] / cell_size)),
               int(math.floor(p[2] / cell_size)))
        grid.setdefault(key, []).append(i)
    return grid


def _neighbors(centroids, idx, eps, grid, cell_size):
    p = centroids[idx]
    cx = int(math.floor(p[0] / cell_size))
    cy = int(math.floor(p[1] / cell_size))
    cz = int(math.floor(p[2] / cell_size))
    r = int(math.ceil(eps / cell_size)) + 1
    out = []
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            for dz in range(-r, r + 1):
                key = (cx + dx, cy + dy, cz + dz)
                if key not in grid: continue
                for j in grid[key]:
                    if j == idx: continue
                    d = math.sqrt((centroids[idx][0] - centroids[j][0]) ** 2
                                  + (centroids[idx][1] - centroids[j][1]) ** 2
                                  + (centroids[idx][2] - centroids[j][2]) ** 2)
                    if d <= eps:
                        out.append(j)
    return out


def _dbscan_grid(centroids, eps, min_samples):
    n = len(centroids)
    labels = [-1] * n
    visited = [False] * n
    cell_size = eps  # cell of size eps for grid efficiency
    grid = _build_grid(centroids, cell_size)
    next_id = 0
    for i in range(n):
        if visited[i]: continue
        visited[i] = True
        nbrs = _neighbors(centroids, i, eps, grid, cell_size)
        if len(nbrs) + 1 < min_samples:
            labels[i] = -1
            continue
        cid = next_id; next_id += 1
        labels[i] = cid
        seeds = list(nbrs)
        idx = 0
        while idx < len(seeds):
            q = seeds[idx]; idx += 1
            if not visited[q]:
                visited[q] = True
                qnbrs = _neighbors(centroids, q, eps, grid, cell_size)
                if len(qnbrs) + 1 >= min_samples:
                    for nb in qnbrs:
                        if nb not in seeds: seeds.append(nb)
            if labels[q] == -1:
                labels[q] = cid
    return labels


def _bbox_world(obj):
    lo = Vector((float("inf"),)*3); hi = Vector((-float("inf"),)*3)
    for c in obj.bound_box:
        p = obj.matrix_world @ Vector(c)
        lo = Vector(map(min, lo, p)); hi = Vector(map(max, hi, p))
    return lo, hi


def _bbox_contains(outer_lo, outer_hi, inner_lo, inner_hi, slack=0.05):
    """Does outer bbox fully contain inner (with slack tolerance)?"""
    for ax in range(3):
        if inner_lo[ax] < outer_lo[ax] - slack: return False
        if inner_hi[ax] > outer_hi[ax] + slack: return False
    return True


def merge_clusters_in_scene(eps_cm=1.0, do_containment_merge=True, do_join=True,
                              min_part_volume_cm3=0.0):
    """Cluster mesh objects in the scene by tight spatial proximity, then
    optionally merge by bbox containment, then JOIN each cluster's meshes
    into one Blender object.

    Returns dict with cluster stats + post-join object count.
    """
    # Collect candidate objects (skip helpers)
    candidates = []
    for o in bpy.data.objects:
        if o.type != "MESH": continue
        if o.name.startswith("SPATAIL_") or o.name in ("HeroCam", "Camera"): continue
        if not o.data.vertices: continue
        candidates.append(o)
    n_pre = len(candidates)

    # Compute per-object bbox + centroid
    objs_bbox = {}
    centroids = []
    for o in candidates:
        lo, hi = _bbox_world(o)
        c = (lo + hi) * 0.5
        vol = (hi.x - lo.x) * (hi.y - lo.y) * (hi.z - lo.z)
        objs_bbox[o.name] = (lo, hi, vol)
        centroids.append((c.x, c.y, c.z))

    # Stage 1: tight DBSCAN
    labels = _dbscan_grid(centroids, eps_cm, min_samples=2)
    # Promote singletons (label -1) to their own cluster IDs so every part has a label
    next_singleton = max([l for l in labels if l >= 0] + [-1]) + 1
    for i in range(len(labels)):
        if labels[i] == -1:
            labels[i] = next_singleton
            next_singleton += 1

    # Group objects by cluster id
    clusters = {}
    for o, lbl in zip(candidates, labels):
        clusters.setdefault(lbl, []).append(o)

    n_after_dbscan = len(clusters)

    # Stage 2: bbox-containment merge
    n_merged = 0
    if do_containment_merge and len(clusters) > 1:
        # Compute per-cluster bbox + volume
        cluster_bbox = {}
        for cid, members in clusters.items():
            lo = Vector((float("inf"),)*3); hi = Vector((-float("inf"),)*3)
            for m in members:
                m_lo, m_hi, _ = objs_bbox[m.name]
                lo = Vector(map(min, lo, m_lo))
                hi = Vector(map(max, hi, m_hi))
            vol = (hi.x - lo.x) * (hi.y - lo.y) * (hi.z - lo.z)
            cluster_bbox[cid] = (lo, hi, vol)
        # Sort clusters by volume descending (largest "containers" first)
        sorted_cids = sorted(cluster_bbox.keys(), key=lambda c: -cluster_bbox[c][2])
        merge_into = {}  # smaller cluster -> larger cluster
        for i_outer, outer_cid in enumerate(sorted_cids):
            o_lo, o_hi, o_vol = cluster_bbox[outer_cid]
            for inner_cid in sorted_cids[i_outer + 1:]:
                if inner_cid in merge_into: continue
                i_lo, i_hi, i_vol = cluster_bbox[inner_cid]
                if i_vol > o_vol * 0.5: continue  # too big to be "inside"
                if _bbox_contains(o_lo, o_hi, i_lo, i_hi, slack=0.5):
                    merge_into[inner_cid] = outer_cid
                    n_merged += 1
        # Apply merges
        for inner_cid, outer_cid in merge_into.items():
            while outer_cid in merge_into:
                outer_cid = merge_into[outer_cid]
            clusters[outer_cid].extend(clusters[inner_cid])
            del clusters[inner_cid]

    n_after_containment = len(clusters)

    # Stage 3: JOIN each cluster's meshes
    n_joined_clusters = 0
    n_joined_parts = 0
    joined_log = []
    if do_join:
        for cid, members in list(clusters.items()):
            if len(members) < 2:
                joined_log.append({"cluster": cid, "joined_into": members[0].name,
                                    "member_count": 1})
                continue
            # Pick the largest-bbox member as the active object (it becomes the join target)
            members_sorted = sorted(members, key=lambda m: -objs_bbox[m.name][2])
            target = members_sorted[0]
            # Deselect everything via view layer (safer when context.selected_objects unavailable)
            for so in bpy.data.objects:
                try: so.select_set(False)
                except Exception: pass
            # Select all members
            for m in members:
                try: m.select_set(True)
                except Exception: pass
            bpy.context.view_layer.objects.active = target
            try:
                with bpy.context.temp_override(active_object=target,
                                                 selected_objects=list(members),
                                                 selected_editable_objects=list(members)):
                    bpy.ops.object.join()
                # Rename joined to a cluster-aware name
                target.name = f"cluster_{cid:05d}"
                n_joined_clusters += 1
                n_joined_parts += len(members)
                joined_log.append({"cluster": cid, "joined_into": target.name,
                                    "member_count": len(members)})
            except Exception as e:
                joined_log.append({"cluster": cid, "error": str(e)})

    # Final mesh count
    n_post = sum(1 for o in bpy.data.objects
                  if o.type == "MESH" and not o.name.startswith("SPATAIL_")
                  and o.name not in ("HeroCam", "Camera"))

    print(f"[merge_clusters] {n_pre} → {n_after_dbscan} (tight DBSCAN) "
          f"→ {n_after_containment} (containment merge) → {n_post} (post-join)")
    return {
        "input_objects": n_pre,
        "after_tight_dbscan": n_after_dbscan,
        "containment_merges": n_merged,
        "after_containment_merge": n_after_containment,
        "joined_clusters": n_joined_clusters,
        "joined_parts": n_joined_parts,
        "final_object_count": n_post,
        "join_log_first_10": joined_log[:10],
    }


print("[spatail_merge_clusters] module loaded.")
