"""
spatail_merge_touching.py — intelligence-driven mesh merge.

The smartest "same physical part" signal isn't centroid proximity — it's
**vertex touching**. Two halves of one button share a boundary line. The
top cap and the toothed ring of a rotary knob share their inner rim.
Two physically separate buttons sitting 5mm apart DON'T share vertices.

Two-pass:
  1. Vertex-touching: meshes with any vertex pair within ε (~2mm) get
     union-find'd into the same component.
  2. Concentric-axis: for components that ended up close but didn't touch
     (e.g., teeth ring with a 1mm gap to the cap), check if their PCA
     principal axes are aligned AND their centroids project onto the same
     line — meaning they're stacked concentrically (rotary-knob case).

Then JOIN each multi-member component into a single Blender object.

USAGE in Blender:
    exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_merge_touching.py").read())
    merge_touching_in_scene(eps_cm=0.2)
"""
import bpy, math, random
from mathutils import Vector


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────

def _bbox_world(obj):
    lo = Vector((float("inf"),) * 3)
    hi = Vector((-float("inf"),) * 3)
    for c in obj.bound_box:
        p = obj.matrix_world @ Vector(c)
        lo = Vector(map(min, lo, p))
        hi = Vector(map(max, hi, p))
    return lo, hi


def _bbox_overlap(lo_a, hi_a, lo_b, hi_b, slack=0.0):
    for ax in range(3):
        if hi_a[ax] + slack < lo_b[ax]:
            return False
        if lo_a[ax] - slack > hi_b[ax]:
            return False
    return True


class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        return True


def _collect_candidates():
    out = []
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        if o.name.startswith("SPATAIL_") or o.name in ("HeroCam", "Camera"):
            continue
        if not o.data.vertices:
            continue
        out.append(o)
    return out


def _world_verts(obj):
    M = obj.matrix_world
    return [M @ v.co for v in obj.data.vertices]


# ────────────────────────────────────────────────────────────────────────
# Pass 1: vertex-touching
# ────────────────────────────────────────────────────────────────────────

def _vertex_touching_merge(objs, eps, min_shared_verts=6, min_shared_frac=0.20):
    """Union-find by *strength* of shared boundary.

    A pair only merges if:
      - they share >= ``min_shared_verts`` vertices within ``eps``, AND
      - the shared count is >= ``min_shared_frac`` of the smaller mesh's vertex count.

    This filters out incidental T-junction or corner-vertex sharing that
    creates spurious transitive closure across the whole asset.
    """
    n = len(objs)
    uf = _UnionFind(n)
    bboxes = [_bbox_world(o) for o in objs]

    # Coarse bbox grid to find candidate pairs cheaply
    coarse = max(eps * 50, 2.0)
    bgrid = {}
    for i, (lo, hi) in enumerate(bboxes):
        for cx in range(int(math.floor(lo.x / coarse)), int(math.floor(hi.x / coarse)) + 1):
            for cy in range(int(math.floor(lo.y / coarse)), int(math.floor(hi.y / coarse)) + 1):
                for cz in range(int(math.floor(lo.z / coarse)), int(math.floor(hi.z / coarse)) + 1):
                    bgrid.setdefault((cx, cy, cz), []).append(i)

    candidates = set()
    for cell_objs in bgrid.values():
        if len(cell_objs) < 2:
            continue
        for ai in range(len(cell_objs)):
            for bi in range(ai + 1, len(cell_objs)):
                a, b = cell_objs[ai], cell_objs[bi]
                lo_a, hi_a = bboxes[a]
                lo_b, hi_b = bboxes[b]
                if _bbox_overlap(lo_a, hi_a, lo_b, hi_b, slack=eps):
                    candidates.add((min(a, b), max(a, b)))

    # Cache world verts only for meshes that appear in candidates
    needed = set()
    for a, b in candidates:
        needed.add(a)
        needed.add(b)
    vcache = {i: _world_verts(objs[i]) for i in needed}

    cell = eps
    eps_sq = eps * eps

    n_pairs_evaluated = 0
    n_pairs_merged = 0
    for a, b in candidates:
        # Note: we DON'T skip already-unioned pairs here, because counting
        # contacts could reveal stronger evidence.  For perf we still skip.
        if uf.find(a) == uf.find(b):
            continue
        n_pairs_evaluated += 1
        # Build hash of A's verts in cells of size eps
        ha = {}
        for p in vcache[a]:
            key = (int(math.floor(p.x / cell)),
                   int(math.floor(p.y / cell)),
                   int(math.floor(p.z / cell)))
            ha.setdefault(key, []).append(p)
        shared = 0
        for p in vcache[b]:
            kx = int(math.floor(p.x / cell))
            ky = int(math.floor(p.y / cell))
            kz = int(math.floor(p.z / cell))
            hit = False
            for dx in (-1, 0, 1):
                if hit: break
                for dy in (-1, 0, 1):
                    if hit: break
                    for dz in (-1, 0, 1):
                        bucket = ha.get((kx + dx, ky + dy, kz + dz))
                        if not bucket:
                            continue
                        for q in bucket:
                            d = (p.x - q.x) ** 2 + (p.y - q.y) ** 2 + (p.z - q.z) ** 2
                            if d <= eps_sq:
                                shared += 1
                                hit = True
                                break
                        if hit: break
        smaller_n = min(len(vcache[a]), len(vcache[b]))
        if smaller_n == 0:
            continue
        frac = shared / smaller_n
        if shared >= min_shared_verts and frac >= min_shared_frac:
            uf.union(a, b)
            n_pairs_merged += 1

    print(f"  pairs evaluated={n_pairs_evaluated} merged={n_pairs_merged}")

    comps = {}
    for i in range(n):
        comps.setdefault(uf.find(i), []).append(i)
    return comps, uf


# ────────────────────────────────────────────────────────────────────────
# Pass 2: concentric-axis (PCA principal axis + projection coincidence)
# ────────────────────────────────────────────────────────────────────────

def _pca_axis(verts):
    n = len(verts)
    if n < 3:
        return None, None
    cx = sum(v.x for v in verts) / n
    cy = sum(v.y for v in verts) / n
    cz = sum(v.z for v in verts) / n
    Cxx = Cxy = Cxz = Cyy = Cyz = Czz = 0.0
    for v in verts:
        dx, dy, dz = v.x - cx, v.y - cy, v.z - cz
        Cxx += dx * dx
        Cyy += dy * dy
        Czz += dz * dz
        Cxy += dx * dy
        Cxz += dx * dz
        Cyz += dy * dz
    # Power iteration for dominant eigenvector
    vec = Vector((random.random() - 0.5, random.random() - 0.5, random.random() - 0.5))
    if vec.length < 1e-9:
        vec = Vector((1.0, 0.0, 0.0))
    vec.normalize()
    for _ in range(30):
        nx = Cxx * vec.x + Cxy * vec.y + Cxz * vec.z
        ny = Cxy * vec.x + Cyy * vec.y + Cyz * vec.z
        nz = Cxz * vec.x + Cyz * vec.y + Czz * vec.z
        new = Vector((nx, ny, nz))
        if new.length < 1e-9:
            break
        new.normalize()
        if (new - vec).length < 1e-7:
            vec = new
            break
        vec = new
    return Vector((cx, cy, cz)), vec


def _concentric_axis_merge(objs, comps_dict, uf, radial_eps_cm=0.4,
                            angle_dot_min=0.93, axis_overlap_slack=2.0):
    """For each multi-component pair whose bboxes are close, test concentric:
      - axes are nearly parallel
      - the smaller's centroid lies within radial_eps of the larger's axis line
      - their projections along the axis overlap (or are within slack)
    """
    # Gather component data: centroid, axis, axis-span, bbox
    comp_info = {}
    for root, member_idxs in comps_dict.items():
        all_verts = []
        for i in member_idxs:
            all_verts.extend(_world_verts(objs[i]))
        if len(all_verts) < 3:
            continue
        centroid, axis = _pca_axis(all_verts)
        if axis is None:
            continue
        # axis-span: project all verts onto axis, take min/max
        projs = [(v - centroid).dot(axis) for v in all_verts]
        lo_proj = min(projs)
        hi_proj = max(projs)
        # bbox
        lo = Vector((min(v.x for v in all_verts),
                     min(v.y for v in all_verts),
                     min(v.z for v in all_verts)))
        hi = Vector((max(v.x for v in all_verts),
                     max(v.y for v in all_verts),
                     max(v.z for v in all_verts)))
        comp_info[root] = {
            "centroid": centroid,
            "axis": axis,
            "lo_proj": lo_proj,
            "hi_proj": hi_proj,
            "bbox_lo": lo,
            "bbox_hi": hi,
            "n_verts": len(all_verts),
        }

    # Coarse grid on bboxes to find candidate pairs
    coarse = 4.0  # cm
    bgrid = {}
    keys = list(comp_info.keys())
    for k in keys:
        info = comp_info[k]
        lo, hi = info["bbox_lo"], info["bbox_hi"]
        for cx in range(int(math.floor(lo.x / coarse)), int(math.floor(hi.x / coarse)) + 1):
            for cy in range(int(math.floor(lo.y / coarse)), int(math.floor(hi.y / coarse)) + 1):
                for cz in range(int(math.floor(lo.z / coarse)), int(math.floor(hi.z / coarse)) + 1):
                    bgrid.setdefault((cx, cy, cz), []).append(k)

    tested = set()
    n_concentric = 0
    for cell_keys in bgrid.values():
        if len(cell_keys) < 2:
            continue
        for ai in range(len(cell_keys)):
            for bi in range(ai + 1, len(cell_keys)):
                a, b = cell_keys[ai], cell_keys[bi]
                pair = (min(a, b), max(a, b))
                if pair in tested:
                    continue
                tested.add(pair)
                if uf.find(a) == uf.find(b):
                    continue
                ia, ib = comp_info[a], comp_info[b]
                # Bbox proximity check (allow small gap)
                if not _bbox_overlap(ia["bbox_lo"], ia["bbox_hi"],
                                      ib["bbox_lo"], ib["bbox_hi"], slack=axis_overlap_slack):
                    continue
                # Axis alignment
                if abs(ia["axis"].dot(ib["axis"])) < angle_dot_min:
                    continue
                # Pick the longer axis as the reference line
                len_a = ia["hi_proj"] - ia["lo_proj"]
                len_b = ib["hi_proj"] - ib["lo_proj"]
                if len_a >= len_b:
                    ref, other = ia, ib
                else:
                    ref, other = ib, ia
                # Perpendicular distance from other's centroid to ref's axis line
                delta = other["centroid"] - ref["centroid"]
                along = delta.dot(ref["axis"])
                perp = (delta - ref["axis"] * along).length
                if perp > radial_eps_cm:
                    continue
                # Axis-span overlap or gap < slack
                # Project other's bbox span onto ref axis
                other_lo = (other["centroid"] - ref["centroid"]).dot(ref["axis"]) + other["lo_proj"] * other["axis"].dot(ref["axis"])
                other_hi = (other["centroid"] - ref["centroid"]).dot(ref["axis"]) + other["hi_proj"] * other["axis"].dot(ref["axis"])
                if other_lo > other_hi:
                    other_lo, other_hi = other_hi, other_lo
                gap = max(ref["lo_proj"] - other_hi, other_lo - ref["hi_proj"], 0.0)
                if gap > axis_overlap_slack:
                    continue
                uf.union(a, b)
                n_concentric += 1

    # Rebuild comps
    new_comps = {}
    for k in comps_dict:
        for i in comps_dict[k]:
            new_comps.setdefault(uf.find(i), []).append(i)
    return new_comps, n_concentric


# ────────────────────────────────────────────────────────────────────────
# Join components
# ────────────────────────────────────────────────────────────────────────

def _join_components(objs, comps):
    n_joined_clusters = 0
    n_joined_parts = 0
    for root_idx, members_idx in comps.items():
        if len(members_idx) < 2:
            continue
        members = [objs[i] for i in members_idx]

        # Largest-volume member becomes the target
        def _vol(o):
            lo, hi = _bbox_world(o)
            return (hi.x - lo.x) * (hi.y - lo.y) * (hi.z - lo.z)

        members.sort(key=lambda m: -_vol(m))
        target = members[0]

        for so in bpy.data.objects:
            try:
                so.select_set(False)
            except Exception:
                pass
        for m in members:
            try:
                m.select_set(True)
            except Exception:
                pass
        bpy.context.view_layer.objects.active = target
        try:
            with bpy.context.temp_override(
                active_object=target,
                selected_objects=list(members),
                selected_editable_objects=list(members),
            ):
                bpy.ops.object.join()
            target.name = f"part_{root_idx:05d}"
            n_joined_clusters += 1
            n_joined_parts += len(members)
        except Exception as e:
            print(f"  [join failed for root {root_idx}] {e}")
    return n_joined_clusters, n_joined_parts


# ────────────────────────────────────────────────────────────────────────
# Public entry point
# ────────────────────────────────────────────────────────────────────────

def merge_touching_in_scene(eps_cm=0.005, do_concentric=True,
                             radial_eps_cm=0.8, do_join=True,
                             min_shared_verts=2, min_shared_frac=0.05,
                             angle_dot_min=0.88, axis_overlap_slack=3.0):
    """Intelligence-driven mesh merge.

    Empirically-tuned defaults for tightly-welded CADs (steering wheel-class):
      eps_cm=0.005           vertex-touching tolerance (50 microns)
      radial_eps_cm=0.8      concentric-axis radial tolerance (8 mm)
      min_shared_verts=2     pair must share ≥ this many verts to merge
      min_shared_frac=0.05   shared count must be ≥ this fraction of smaller mesh
      angle_dot_min=0.88     concentric axes must align to this dot product
      axis_overlap_slack=3.0 max along-axis gap between concentric components (cm)

    For looser CADs (engine, mechanical), raise min_shared_verts to 6 and
    min_shared_frac to 0.2 to avoid over-merge.
    """
    objs = _collect_candidates()
    n_pre = len(objs)
    print(f"[merge_touching] starting from {n_pre} mesh objects")

    print(f"[merge_touching] Pass 1: vertex-touching (eps={eps_cm} cm, "
          f"min_shared_verts={min_shared_verts}, min_shared_frac={min_shared_frac})…")
    comps, uf = _vertex_touching_merge(objs, eps_cm,
                                        min_shared_verts=min_shared_verts,
                                        min_shared_frac=min_shared_frac)
    n_touch = len(comps)
    multi_touch = sum(1 for v in comps.values() if len(v) >= 2)
    print(f"  → {n_touch} components ({multi_touch} multi-member)")

    n_concentric = 0
    if do_concentric:
        print(f"[merge_touching] Pass 2: concentric-axis (radial={radial_eps_cm} cm)…")
        comps, n_concentric = _concentric_axis_merge(objs, comps, uf,
                                                      radial_eps_cm=radial_eps_cm,
                                                      angle_dot_min=angle_dot_min,
                                                      axis_overlap_slack=axis_overlap_slack)
        print(f"  → {n_concentric} concentric merges, {len(comps)} components remain")

    n_components_final = len(comps)
    multi_final = sum(1 for v in comps.values() if len(v) >= 2)

    n_joined_clusters = 0
    n_joined_parts = 0
    if do_join:
        print(f"[merge_touching] Pass 3: JOIN {multi_final} multi-part components…")
        n_joined_clusters, n_joined_parts = _join_components(objs, comps)

    n_post = sum(1 for o in bpy.data.objects
                  if o.type == "MESH"
                  and not o.name.startswith("SPATAIL_")
                  and o.name not in ("HeroCam", "Camera"))

    print(f"[merge_touching] {n_pre} → {n_components_final} → {n_post} (post-join)")
    return {
        "input_objects": n_pre,
        "after_touching": n_touch,
        "concentric_merges": n_concentric,
        "after_concentric": n_components_final,
        "joined_clusters": n_joined_clusters,
        "joined_parts": n_joined_parts,
        "final_object_count": n_post,
    }


print("[spatail_merge_touching] module loaded.")
