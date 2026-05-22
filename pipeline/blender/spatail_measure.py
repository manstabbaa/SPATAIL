"""
spatail_measure.py — the SPATAIL measuring stage, as a Blender module.

WHERE THIS LIVES: inside the running Blender session. The agent loads
this file via execute_blender_code:

    exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_measure.py").read())
    out = measure(prompt_category="four_stroke_motion",
                  parts_json=r"C:/.../v10_engine.parts.json",
                  out_json=r"C:/.../v10_engine.measurements.four_stroke_motion.json")

WHY IN BLENDER: the agent already has the CAD loaded + segmented + named
in the live scene. Spawning a headless Blender to re-load the same CAD
just to read its geometry is wasteful and breaks the live-iteration
loop. The measuring stage IS a Blender operation; this file is the
function library the agent calls.

CONTRACT:
  measure(prompt_category, ...) → measurements: dict
  The dict matches what the named category's reader expects. Writing
  the dict to disk is the caller's job (so the agent can preview + tweak
  before persisting).

SUPPORTED CATEGORIES (closed set, additive):
  four_stroke_motion   — crank axis, journals, throw radii, bore axes,
                         rest pin / journal / piston positions, stroke
  cross_section        — target bbox, principal axes via PCA, suggested
                         cut planes (axis + offsets) per principal axis
  exploded_view        — per-part centroid, radial direction from asset
                         centroid, suggested explode distance per part
  scale_reference      — asset bbox in metres, suggested reference object
                         (human silhouette / coin / hand / iphone)
  assembly_sequence    — STUB: returns adjacency graph + Z-stratification
                         skeleton; full topological ordering is v0.2.

Every category returns:
  {
    "promptCategory": "...",
    "asset": "...",
    "schemaVersion": "0.2.0-spatail-measure",
    "computedAt": "ISO-8601",
    "summary": { ... high-level numbers the agent cares about ... },
    "<category-specific fields>": ...
  }
"""

import bpy, json, math, os
from datetime import datetime, timezone
from mathutils import Vector


# ===========================================================================
# Geometry helpers — shared across categories
# ===========================================================================

def world_vertices(obj):
    wm = obj.matrix_world
    for v in obj.data.vertices:
        yield wm @ v.co


def bbox_world(obj):
    """Returns (lo, hi) Vectors in world space."""
    lo = Vector((float("inf"),)*3)
    hi = Vector((-float("inf"),)*3)
    for c in obj.bound_box:
        p = obj.matrix_world @ Vector(c)
        lo = Vector(map(min, lo, p))
        hi = Vector(map(max, hi, p))
    return lo, hi


def union_bbox(objs):
    lo = Vector((float("inf"),)*3)
    hi = Vector((-float("inf"),)*3)
    for o in objs:
        a, b = bbox_world(o)
        lo = Vector(map(min, lo, a))
        hi = Vector(map(max, hi, b))
    return lo, hi


def fit_circle_xy(points):
    """Kasa-method least-squares circle fit in the XY plane.

    Solves the linear system  x² + y² = a x + b y + c  in (a,b,c), then
    centre = (a/2, b/2), radius = sqrt(c + (a/2)² + (b/2)²).

    `points` is a list of mathutils.Vector — only .x and .y are used.
    Returns (center_xy: Vector(x,y), radius: float, residual: float).
    """
    n = len(points)
    if n < 3:
        return None, None, float("inf")
    # Normal-equations form to avoid pulling in numpy.
    Sx = Sy = Sxx = Syy = Sxy = Sxxx = Syyy = Sxyy = Syxx = 0.0
    Sx2py2 = Sx_x2py2 = Sy_x2py2 = 0.0
    for p in points:
        x, y = p.x, p.y
        r2 = x*x + y*y
        Sx += x; Sy += y
        Sxx += x*x; Syy += y*y; Sxy += x*y
        Sx2py2 += r2
        Sx_x2py2 += x*r2
        Sy_x2py2 += y*r2
    # System:  [Sxx Sxy Sx] [a]   [Sx_x2py2]
    #          [Sxy Syy Sy] [b] = [Sy_x2py2]
    #          [Sx  Sy  n ] [c]   [Sx2py2 ]
    A = [[Sxx, Sxy, Sx], [Sxy, Syy, Sy], [Sx, Sy, n]]
    rhs = [Sx_x2py2, Sy_x2py2, Sx2py2]
    sol = _solve3x3(A, rhs)
    if sol is None:
        return None, None, float("inf")
    a, b, c = sol
    cx, cy = a / 2.0, b / 2.0
    r2 = c + cx*cx + cy*cy
    if r2 < 0: return None, None, float("inf")
    r = math.sqrt(r2)
    # Mean radial residual
    resid = sum(abs(math.hypot(p.x - cx, p.y - cy) - r) for p in points) / n
    return Vector((cx, cy)), r, resid


def _solve3x3(A, b):
    """Gaussian elimination for a 3x3 system. Returns None if singular."""
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    # Forward elimination
    for i in range(3):
        # partial pivot
        piv = max(range(i, 3), key=lambda r: abs(M[r][i]))
        if abs(M[piv][i]) < 1e-12: return None
        M[i], M[piv] = M[piv], M[i]
        for r in range(i + 1, 3):
            f = M[r][i] / M[i][i]
            for c in range(i, 4):
                M[r][c] -= f * M[i][c]
    # Back-substitution
    x = [0, 0, 0]
    for i in (2, 1, 0):
        s = M[i][3] - sum(M[i][j] * x[j] for j in range(i + 1, 3))
        x[i] = s / M[i][i]
    return x


def pca_principal_axes(points):
    """Return (centroid, axes, eigvals) where axes = [(unit_vec, eigval)]
    sorted by descending eigenvalue. PCA on Vec3s in world coordinates,
    pure-Python power-iteration since we don't want numpy in Blender."""
    n = len(points)
    if n < 4: return Vector((0,0,0)), [], []
    cx = cy = cz = 0.0
    for p in points:
        cx += p.x; cy += p.y; cz += p.z
    cx /= n; cy /= n; cz /= n
    centroid = Vector((cx, cy, cz))
    # Build covariance matrix (3x3)
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
    # Find 3 eigenvalues/vectors via deflation + power iteration
    axes = []
    A = [row[:] for row in M]
    for _ in range(3):
        v = [1.0, 1.0, 1.0]
        for _it in range(60):
            nv = [sum(A[i][j] * v[j] for j in range(3)) for i in range(3)]
            mag = math.sqrt(sum(x*x for x in nv)) or 1.0
            v = [x / mag for x in nv]
        # Eigenvalue (Rayleigh quotient)
        Av = [sum(A[i][j] * v[j] for j in range(3)) for i in range(3)]
        eig = sum(v[i] * Av[i] for i in range(3))
        axes.append((Vector(v), eig))
        # Deflate: A := A - eig * v v^T
        for i in range(3):
            for j in range(3):
                A[i][j] -= eig * v[i] * v[j]
    axes.sort(key=lambda x: -x[1])
    return centroid, [a for a, _ in axes], [e for _, e in axes]


# ===========================================================================
# Part-roster helpers — read the parts.json from previous segmentation
# ===========================================================================

def load_parts(parts_json):
    with open(parts_json) as f:
        return json.load(f)


def bucket_by_role(parts):
    """Return dict of role-name → list of (obj, sem, parts_record)."""
    out = {}
    for p in parts:
        sem = p.get("semantic", {}) or {}
        nm = sem.get("name") or ""
        obj = bpy.data.objects.get(p["id"])
        if obj is None: continue
        if "Crank throw" in nm:        out.setdefault("crank", []).append((obj, sem, p))
        elif "Piston" in nm:           out.setdefault("piston", []).append((obj, sem, p))
        elif "Connecting rod" in nm:   out.setdefault("rod", []).append((obj, sem, p))
        elif "Wrist pin" in nm:        out.setdefault("pin", []).append((obj, sem, p))
        else:                           out.setdefault("other", []).append((obj, sem, p))
    return out


# ===========================================================================
# CATEGORY 1: four_stroke_motion  (full implementation, refined)
# ===========================================================================

def measure_four_stroke_motion(parts):
    """Returns the spec the slider-crank animator consumes.

    Strategy notes:
      - Crank rotation axis = +Z passing through the bbox center of all
        crank-throw bboxes projected to XY. We don't trust PCA's primary
        axis here because the crank parts aren't symmetric (journals +
        counterweights at varied angles bias the PCA centroid).
      - Per-throw journal = the corresponding conrod's big-end ring centre.
        Conrod big-end rings are clean circular features that PCA-based
        endpoint detection nails. Each throw is shared by 2 cylinders
        (bank A + bank B at the same cylinder index), so we average their
        rod big-ends per throw.
      - Per-cylinder bore axis = unit vector from rotation axis to piston
        centroid, projected onto the plane perpendicular to the axis.
      - Stroke = 2 × throw radius (exact for the slider-crank model).
    """
    by_role = bucket_by_role(parts)
    cranks  = by_role.get("crank", [])
    rods    = by_role.get("rod", [])
    pistons = by_role.get("piston", [])
    pins    = by_role.get("pin", [])

    # ----- Crank axis: bbox-centroid average of throws, direction +Z ---
    # Vertex-mean is biased toward dense features (the journal cylinder has
    # more verts per unit volume than the counterweight, which pulls the
    # mean off the rotation axis by ~10cm). Throw bbox-centroids are stable:
    # each throw's bbox contains both the journal and counterweight + the
    # shaft segment, and the centroid sits roughly on the rotation axis.
    # Averaging across 5 throws cancels any per-throw bias.
    if cranks:
        accx = accy = 0.0
        for obj, _, _ in cranks:
            lo, hi = bbox_world(obj)
            accx += (lo.x + hi.x) * 0.5
            accy += (lo.y + hi.y) * 0.5
        axis_origin = Vector((accx / len(cranks), accy / len(cranks), 0))
    else:
        axis_origin = Vector((0, 0, 0))
    axis_dir = Vector((0, 0, 1))

    # ----- Per-throw journal: mean of top-radius angular cluster ------
    # For each throw: project verts onto XY, take top 35% by radius
    # (journal + counterweight tips), split into 2 angular halves
    # opposing each other, pick the half with smaller XY spread (the
    # journal is a tight cylindrical post; the counterweight is a wider
    # slab). Journal centre = mean of that cluster. This is the
    # algorithm that produced visibly correct piston phases in the
    # previous turn — we keep it.
    throws = []
    for obj, sem, p in cranks:
        idx = sem.get("cylinderIndex")
        pts = list(world_vertices(obj))
        radii = [math.hypot(v.x - axis_origin.x, v.y - axis_origin.y) for v in pts]
        rmax = max(radii) if radii else 0
        cutoff = rmax * 0.65
        far = [(pts[i], radii[i]) for i, r in enumerate(radii) if r >= cutoff]
        if not far:
            throws.append({"throwId": obj.name, "cylinderIndex": idx,
                           "status": "no_journal_cluster"})
            continue
        far_angles = [math.atan2(p.y - axis_origin.y, p.x - axis_origin.x)
                      for p, _ in far]
        med = sorted(far_angles)[len(far_angles)//2]
        g1, g2 = [], []
        for (p, _), a in zip(far, far_angles):
            d = abs(((a - med + math.pi) % (2*math.pi)) - math.pi)
            (g1 if d < math.pi/2 else g2).append(p)
        def spread(group):
            if not group: return float("inf")
            cx = sum(g.x for g in group) / len(group)
            cy = sum(g.y for g in group) / len(group)
            return sum(math.hypot(g.x - cx, g.y - cy) for g in group) / len(group)
        journal_pts = g1 if spread(g1) <= spread(g2) else g2
        mean_x = sum(p.x for p in journal_pts) / len(journal_pts)
        mean_y = sum(p.y for p in journal_pts) / len(journal_pts)
        mean_z = sum(p.z for p in journal_pts) / len(journal_pts)
        journal_world = Vector((mean_x, mean_y, mean_z))
        offset = math.hypot(mean_x - axis_origin.x, mean_y - axis_origin.y)
        phase = math.degrees(math.atan2(mean_y - axis_origin.y, mean_x - axis_origin.x))
        throws.append({
            "throwId": obj.name,
            "cylinderIndex": idx,
            "journalCentreWorld": [round(c, 4) for c in journal_world],
            "throwRadius_cm": round(offset, 4),
            "rotationalPhaseDeg": round(phase, 2),
            "derivedFrom": "mean of top-radius angular cluster (~35%)",
        })

    # ----- Per-conrod endpoints via PCA (kept from refined pass) -------
    rod_endpoints = {}
    for rod_obj, _, _ in rods:
        rod_pts = list(world_vertices(rod_obj))
        rc, raxes, _ = pca_principal_axes(rod_pts)
        long_axis = raxes[0] if raxes else Vector((0, 1, 0))
        projs = sorted([(v.dot(long_axis), v) for v in rod_pts], key=lambda x: x[0])
        k = max(3, int(len(projs) * 0.05))
        end_lo_pts = [v for _, v in projs[:k]]
        end_hi_pts = [v for _, v in projs[-k:]]
        end_lo = sum(end_lo_pts, Vector((0,0,0))) / len(end_lo_pts)
        end_hi = sum(end_hi_pts, Vector((0,0,0))) / len(end_hi_pts)
        def axial_radius(v):
            return math.hypot(v.x - axis_origin.x, v.y - axis_origin.y)
        if axial_radius(end_lo) < axial_radius(end_hi):
            big_end, small_end = end_lo, end_hi
        else:
            big_end, small_end = end_hi, end_lo
        rod_endpoints[rod_obj.name] = (small_end, big_end)

    # ----- Per-cylinder spec: USE OBJ-AUTHORED PISTON POSITIONS --------
    # Earlier attempts (3D-proximity matching, then first-principles slot
    # generation) both produced visually-wrong layouts. The OBJ's piston
    # meshes ARE positioned at sensible rest-pose locations (a mid-cycle
    # snapshot the author placed them at). Trust those positions:
    #
    #   1. Pair pistons by Z proximity (10 → 5 pairs, one per cyl)
    #   2. Within each pair, larger-X = bank B, smaller-X = bank A
    #   3. Match each pair to the throw at the same Z
    #   4. boreAxisUnit per piston = (piston_pos − journal_pos) projected
    #      perp to crank axis, normalized — i.e. the actual rod direction
    #      at rest, projected into the bore plane
    #   5. restWristPinWorld = piston mesh's bbox centroid
    #   6. conrodLength_cm = PCA-measured average (~17cm) across all rods
    #
    # The animator then drives the slider-crank from this rest pose: as
    # the crank rotates, each journal orbits, projecting back onto the
    # bore axis to compute the new piston position.
    throw_list = sorted([t for t in throws if "journalCentreWorld" in t],
                        key=lambda t: -t["journalCentreWorld"][2])  # high-Z first = cyl 1
    rod_length = 17.0313  # PCA-measured average (consistent across rods)
    if rod_endpoints:
        lens = [(v[1] - v[0]).length for v in rod_endpoints.values()]
        rod_length = sum(lens) / len(lens)

    # Piston rest positions (bbox centroids) sorted by Z descending
    piston_records = []
    for pobj, _, _ in pistons:
        lo, hi = bbox_world(pobj)
        pc = (lo + hi) * 0.5
        piston_records.append({"obj": pobj, "pos": pc})
    piston_records.sort(key=lambda r: -r["pos"].z)
    # Form 5 Z-pairs, within each pair the +X one is bank B
    pair_count = len(piston_records) // 2
    paired = []
    for i in range(pair_count):
        p1, p2 = piston_records[2*i], piston_records[2*i+1]
        a, b = (p2, p1) if p2["pos"].x < p1["pos"].x else (p1, p2)
        paired.append((a, b))
    # Single rod pool — rods are interchangeable shape providers
    rod_pool = [r for r,_,_ in rods]

    # Build cylinder slots from OBJ-authored piston positions. Each cyl
    # gets its OWN bore axis from the actual rod direction at rest. NO
    # bank averaging, NO snapping pistons to derived positions — the OBJ
    # author's rest pose is respected. The animator drives motion along
    # the per-cyl bore axis via slider-crank projection.
    cylinders = []
    for pair_idx, (pist_a, pist_b) in enumerate(paired):
        if not throw_list: break
        target_z = (pist_a["pos"].z + pist_b["pos"].z) * 0.5
        throw = min(throw_list, key=lambda t: abs(t["journalCentreWorld"][2] - target_z))
        journal = Vector(throw["journalCentreWorld"])
        for bank, pist_rec in (("A", pist_a), ("B", pist_b)):
            if not rod_pool: continue
            piston_obj = pist_rec["obj"]
            piston_pos = pist_rec["pos"]
            # bore_unit: piston-direction-from-journal projected perp to crank axis
            rod_vec = piston_pos - journal
            rod_vec_perp = rod_vec - axis_dir * rod_vec.dot(axis_dir)
            if rod_vec_perp.length < 1e-4: continue
            bore_unit = rod_vec_perp.normalized()
            rod_obj = rod_pool.pop(0)
            # Rod endpoints from this rod's PCA (so the rig can set the
            # rod's origin to its small end for correct Damped Track pivot)
            small_end, big_end = (None, None)
            if rod_obj.name in rod_endpoints:
                s_e, b_e = rod_endpoints[rod_obj.name]
                small_end = [round(c, 4) for c in s_e]
                big_end   = [round(c, 4) for c in b_e]
            cylinders.append({
                "cylinderIndex": throw["cylinderIndex"],
                "bank": bank,
                "rodId": rod_obj.name,
                "pistonId": piston_obj.name,
                "boreAxisUnit": [round(c, 4) for c in bore_unit],
                "restPistonWorld":   [round(c, 4) for c in piston_pos],
                "restRodBigEnd":     big_end or [round(c, 4) for c in journal],
                "restRodSmallEnd":   small_end or [round(c, 4) for c in piston_pos],
                "conrodLength_cm": round(rod_length, 4),
                "throwId": throw["throwId"],
                "restJournalPosition": throw["journalCentreWorld"],
                "derivedFrom": "OBJ-authored piston rest positions; per-cyl bore_unit; rod endpoints from PCA for rig pivot",
            })

    radii = [t["throwRadius_cm"] for t in throws if "throwRadius_cm" in t]
    summary = {
        "throwCount": len(throws),
        "cylinderCount": len(cylinders),
        "throwRadiusMin_cm": round(min(radii), 4) if radii else None,
        "throwRadiusMax_cm": round(max(radii), 4) if radii else None,
        "throwRadiusAvg_cm": round(sum(radii)/len(radii), 4) if radii else None,
        "stroke_cm": round(2 * sum(radii)/len(radii), 4) if radii else None,
        "conrodLength_cm": round(rod_length, 4),
        "rodToStrokeRatio": round(rod_length / (2 * sum(radii)/len(radii)), 3)
                            if radii else None,
        "slotGeneration": "v3-restore: OBJ-authored positions; per-cyl bore axis; rod endpoints exposed for small-end pivot rigging",
    }
    return {
        "promptCategory": "four_stroke_motion",
        "crank": {
            "axisOriginWorld":     [round(c, 4) for c in axis_origin],
            "axisDirectionWorld":  [round(c, 4) for c in axis_dir],
        },
        "throws": throws,
        "cylinders": cylinders,
        "summary": summary,
    }


# ===========================================================================
# CATEGORY 2: cross_section  (full)
# ===========================================================================

def measure_cross_section(parts, target_id=None):
    """Compute PCA axes of the (optionally) named target, plus suggested
    cut plane offsets per principal axis. If target_id is None, uses the
    union of all parts."""
    if target_id:
        obj = bpy.data.objects.get(target_id)
        objs = [obj] if obj else []
    else:
        objs = [bpy.data.objects.get(p["id"]) for p in parts]
        objs = [o for o in objs if o is not None]
    if not objs:
        return {"promptCategory": "cross_section", "error": "no target geometry"}
    all_pts = []
    for o in objs:
        for v in world_vertices(o):
            all_pts.append(v)
    lo, hi = union_bbox(objs)
    centroid, axes, eigvals = pca_principal_axes(all_pts)
    bbox_size = hi - lo
    suggested_cuts = []
    for i, (ax, ev) in enumerate(zip(axes, eigvals)):
        # Project bbox onto axis to get extents
        proj = [(v - centroid).dot(ax) for v in all_pts]
        pmin, pmax = min(proj), max(proj)
        # 3 suggested cuts along this axis: at 25%, 50%, 75% of the extent
        cuts = [pmin + (pmax - pmin) * f for f in (0.25, 0.5, 0.75)]
        suggested_cuts.append({
            "axisRank": i + 1,                              # 1 = longest dimension
            "axisDirectionWorld": [round(c, 4) for c in ax],
            "extent_cm": round(pmax - pmin, 4),
            "suggestedCutOffsetsAlongAxis_cm": [round(c, 4) for c in cuts],
            "eigenvalue": round(ev, 6),
        })
    return {
        "promptCategory": "cross_section",
        "target": target_id or "union",
        "bboxLo": [round(c, 4) for c in lo],
        "bboxHi": [round(c, 4) for c in hi],
        "bboxSize_cm": [round(c, 4) for c in bbox_size],
        "centroidWorld": [round(c, 4) for c in centroid],
        "principalAxes": suggested_cuts,
        "summary": {
            "longestAxisDir": [round(c, 4) for c in (axes[0] if axes else Vector((0,0,1)))],
            "longestExtent_cm": round(suggested_cuts[0]["extent_cm"], 4) if suggested_cuts else None,
        },
    }


# ===========================================================================
# CATEGORY 3: exploded_view  (full)
# ===========================================================================

def measure_exploded_view(parts):
    """For each part, compute its centroid, its direction from the asset
    centroid, and a suggested explode distance proportional to the asset
    size. The animator can then drive each part outward along that
    direction."""
    objs = [bpy.data.objects.get(p["id"]) for p in parts]
    objs = [o for o in objs if o is not None]
    if not objs:
        return {"promptCategory": "exploded_view", "error": "no parts"}
    # Asset centroid + scale
    all_pts = []
    for o in objs:
        for v in world_vertices(o):
            all_pts.append(v)
    asset_centroid, _, _ = pca_principal_axes(all_pts)
    lo, hi = union_bbox(objs)
    diag = (hi - lo).length
    suggested_spread = diag * 0.35  # ~1/3 of the asset's longest diagonal

    per_part = []
    for p in parts:
        obj = bpy.data.objects.get(p["id"])
        if obj is None: continue
        pc = obj.matrix_world.translation
        d = pc - asset_centroid
        dist = d.length
        direction = d.normalized() if dist > 1e-4 else Vector((0, 0, 1))
        # Group by Z-stratum so the agent can sequence the explode left→right
        stratum_key = round((pc.z - lo.z) / max(1e-4, hi.z - lo.z), 2)
        per_part.append({
            "partId": p["id"],
            "centroidWorld": [round(c, 4) for c in pc],
            "directionFromAssetCentroid": [round(c, 4) for c in direction],
            "currentDistanceFromCentroid_cm": round(dist, 4),
            "suggestedExplodeOffset_cm": round(suggested_spread, 4),
            "explodedTargetWorld": [round(c, 4) for c in (pc + direction * suggested_spread)],
            "stratum_z": stratum_key,
        })
    return {
        "promptCategory": "exploded_view",
        "assetCentroidWorld": [round(c, 4) for c in asset_centroid],
        "assetBboxDiagonal_cm": round(diag, 4),
        "suggestedSpread_cm": round(suggested_spread, 4),
        "parts": per_part,
        "summary": {
            "partCount": len(per_part),
            "suggestedSequenceByStratum":
                sorted({p["stratum_z"] for p in per_part}),
        },
    }


# ===========================================================================
# CATEGORY 4: scale_reference  (full)
# ===========================================================================

def measure_scale_reference(parts):
    """Estimate physical scale + suggest a reference object the agent can
    place beside the asset for size context."""
    objs = [bpy.data.objects.get(p["id"]) for p in parts]
    objs = [o for o in objs if o is not None]
    if not objs:
        return {"promptCategory": "scale_reference", "error": "no parts"}
    lo, hi = union_bbox(objs)
    size = hi - lo
    longest_cm = max(size)
    # Unit-guess: CAD often comes in mm or cm. If the longest extent is
    # >500 (in CAD units), the asset is probably in mm; <500 likely cm.
    unit_guess = "mm" if longest_cm > 500 else "cm"
    longest_m = longest_cm / (1000.0 if unit_guess == "mm" else 100.0)
    # Pick a reference object based on the asset's longest extent in metres
    if longest_m > 3.0:
        ref = {"object": "human", "longest_m": 1.75, "label": "human silhouette (1.75 m)"}
    elif longest_m > 1.0:
        ref = {"object": "human", "longest_m": 1.75, "label": "human silhouette (1.75 m)"}
    elif longest_m > 0.3:
        ref = {"object": "iphone", "longest_m": 0.146, "label": "iPhone (14.6 cm)"}
    elif longest_m > 0.05:
        ref = {"object": "hand", "longest_m": 0.18, "label": "human hand (~18 cm)"}
    else:
        ref = {"object": "coin", "longest_m": 0.024, "label": "Australian $2 coin (24 mm)"}
    return {
        "promptCategory": "scale_reference",
        "bboxSize_cadUnits": [round(c, 4) for c in size],
        "longestExtent_cadUnits": round(longest_cm, 4),
        "unitGuess": unit_guess,
        "longestExtent_m": round(longest_m, 4),
        "suggestedReference": ref,
        "summary": {
            "assetLongest_m": round(longest_m, 4),
            "referenceObject": ref["object"],
            "referenceRatio": round(longest_m / ref["longest_m"], 2),
        },
    }


# ===========================================================================
# CATEGORY 5: assembly_sequence  (stub — v0.2 will do topology proper)
# ===========================================================================

def measure_assembly_sequence(parts):
    """STUB: returns the adjacency graph skeleton (parts whose bboxes
    touch or overlap) + a Z-strata ordering. A full topological sort
    needs contact-surface inference (real CAD constraint solver) — that's
    v0.2 work. For today the agent can use the strata as a "what plausibly
    bolts on first" hint."""
    objs = [(p["id"], bpy.data.objects.get(p["id"])) for p in parts]
    objs = [(pid, o) for pid, o in objs if o is not None]
    # Z-strata: sort parts by Z centroid
    by_z = sorted(objs, key=lambda x: x[1].matrix_world.translation.z)
    # Naive adjacency: bboxes whose XY centroids are within `dist_thresh`
    union_lo, union_hi = union_bbox([o for _, o in objs])
    dist_thresh = (union_hi - union_lo).length * 0.05
    centroids = {pid: o.matrix_world.translation for pid, o in objs}
    adjacency = []
    pids = [pid for pid, _ in objs]
    for i, a in enumerate(pids):
        for b in pids[i+1:]:
            d = (centroids[a] - centroids[b]).length
            if d < dist_thresh:
                adjacency.append({"a": a, "b": b, "distance_cm": round(d, 4)})
    return {
        "promptCategory": "assembly_sequence",
        "status": "stub_v0_1",
        "note": ("Full topological assembly ordering needs contact-surface "
                 "inference (proper CAD constraint solver). This v0.1 returns "
                 "the Z-stratification + bbox adjacency as a starting hint."),
        "zStratumOrder": [pid for pid, _ in by_z],
        "bboxAdjacency": adjacency,
        "summary": {
            "partCount": len(objs),
            "adjacencyEdges": len(adjacency),
            "adjacencyThreshold_cm": round(dist_thresh, 4),
        },
    }


# ===========================================================================
# Dispatch
# ===========================================================================

CATEGORIES = {
    "four_stroke_motion": measure_four_stroke_motion,
    "cross_section":      measure_cross_section,
    "exploded_view":      measure_exploded_view,
    "scale_reference":    measure_scale_reference,
    "assembly_sequence":  measure_assembly_sequence,
}


def measure(prompt_category, parts_json=None, out_json=None, **kwargs):
    """Main entry. Loads parts.json, dispatches to the named category,
    optionally writes the result to disk, returns the dict.

        out = measure("four_stroke_motion",
                      parts_json=".../v10_engine.parts.json",
                      out_json=".../v10_engine.measurements.four_stroke_motion.json")
    """
    if prompt_category not in CATEGORIES:
        raise ValueError(f"unknown prompt_category '{prompt_category}'. "
                         f"Allowed: {list(CATEGORIES)}")
    parts_data = load_parts(parts_json) if parts_json else {"parts": []}
    parts = parts_data.get("parts", [])
    fn = CATEGORIES[prompt_category]
    # Call with category-specific kwargs (e.g. cross_section accepts target_id)
    result = fn(parts, **kwargs) if kwargs else fn(parts)
    asset_id = parts_data.get("assetId", "unknown")
    result.setdefault("asset", asset_id)
    result["schemaVersion"] = "0.2.0-spatail-measure"
    result["computedAt"] = datetime.now(timezone.utc).isoformat()
    if out_json:
        os.makedirs(os.path.dirname(out_json), exist_ok=True)
        with open(out_json, "w") as f:
            json.dump(result, f, indent=2)
        print(f"[spatail_measure] {prompt_category} → {out_json}")
    return result


# ===========================================================================
# Self-introspection: list supported categories + their fields
# ===========================================================================

def categories_manifest():
    """Return a manifest of each category's purpose + expected output fields.
    Useful when the agent picks a category from a prompt + needs to see
    what it'll get."""
    return {
        "four_stroke_motion": {
            "purpose": "Slider-crank kinematics: drive a reciprocating engine's pistons + crank.",
            "outputs": ["crank.axisOriginWorld", "crank.axisDirectionWorld",
                        "throws[*].journalCentreWorld", "throws[*].throwRadius_cm",
                        "throws[*].rotationalPhaseDeg",
                        "cylinders[*].boreAxisUnit", "cylinders[*].conrodLength_cm",
                        "summary.stroke_cm", "summary.rodToStrokeRatio"],
            "requires": "parts.json with semantic labels for crank/piston/rod/pin",
        },
        "cross_section": {
            "purpose": "Pick a slicing plane through an object via PCA principal axes.",
            "outputs": ["principalAxes[*].axisDirectionWorld",
                        "principalAxes[*].suggestedCutOffsetsAlongAxis_cm",
                        "bboxSize_cm", "centroidWorld"],
            "requires": "any geometry in scene; optional target_id",
        },
        "exploded_view": {
            "purpose": "Per-part radial-outward direction + spread distance for an explode animation.",
            "outputs": ["parts[*].directionFromAssetCentroid",
                        "parts[*].suggestedExplodeOffset_cm",
                        "parts[*].explodedTargetWorld", "parts[*].stratum_z"],
            "requires": "parts.json with at least one part",
        },
        "scale_reference": {
            "purpose": "Estimate physical scale + suggest a reference object (human/iphone/hand/coin).",
            "outputs": ["unitGuess", "longestExtent_m", "suggestedReference"],
            "requires": "parts.json with any geometry",
        },
        "assembly_sequence": {
            "purpose": "(v0.1 stub) Z-stratification + bbox adjacency hint for assembly order.",
            "outputs": ["zStratumOrder", "bboxAdjacency"],
            "requires": "parts.json; full topology in v0.2",
        },
    }


print("[spatail_measure] module loaded. Categories:", list(CATEGORIES))
