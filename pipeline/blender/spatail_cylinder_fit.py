"""
spatail_cylinder_fit.py — RANSAC + Kasa circle fitting for joint axes.

For a crank throw, conrod big-end, conrod small-end, or any cylindrical
joint feature, we want to know the AXIS of the cylinder to sub-mm
accuracy. Since most engine joints have axes parallel to a known
direction (the crank Z axis, or the rod's long axis), the problem
reduces to a 2D CIRCLE FIT in the plane perpendicular to that axis.

Algorithm:
  1. Project candidate vertices onto the plane perpendicular to the
     known axis direction.
  2. Filter to a relevant subset (e.g. high-radius vertices for
     journals, or vertices near one end for rod rings).
  3. RANSAC: sample 3 points → fit a unique circle → count inliers.
     Reject if radius falls outside the expected radius band.
  4. Refine with Kasa-method least-squares on the inliers.
  5. Return (center, radius, n_inliers, residual_mm).

This module is pure Python — no numpy / scipy. Runs inside Blender.
"""
import math
import random
from mathutils import Vector


def solve_3x3(A, b):
    """Gaussian elimination for a 3x3 linear system. Returns None on singular."""
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for i in range(3):
        piv = max(range(i, 3), key=lambda r: abs(M[r][i]))
        if abs(M[piv][i]) < 1e-12: return None
        M[i], M[piv] = M[piv], M[i]
        for r in range(i + 1, 3):
            f = M[r][i] / M[i][i]
            for c in range(i, 4):
                M[r][c] -= f * M[i][c]
    x = [0.0, 0.0, 0.0]
    for i in (2, 1, 0):
        s = M[i][3] - sum(M[i][j] * x[j] for j in range(i + 1, 3))
        x[i] = s / M[i][i]
    return x


def fit_circle_3pt(p1, p2, p3):
    """Unique circle through 3 non-collinear 2D points. Returns (cx, cy, r)."""
    ax, ay = p1; bx, by = p2; cx_, cy_ = p3
    d = 2 * (ax * (by - cy_) + bx * (cy_ - ay) + cx_ * (ay - by))
    if abs(d) < 1e-9: return None
    sq_a = ax * ax + ay * ay
    sq_b = bx * bx + by * by
    sq_c = cx_ * cx_ + cy_ * cy_
    ux = (sq_a * (by - cy_) + sq_b * (cy_ - ay) + sq_c * (ay - by)) / d
    uy = (sq_a * (cx_ - bx) + sq_b * (ax - cx_) + sq_c * (bx - ax)) / d
    r = math.sqrt((ax - ux) ** 2 + (ay - uy) ** 2)
    return ux, uy, r


def fit_circle_kasa(points):
    """Kasa-method LS circle fit: solves x² + y² = ax + by + c.

    Returns (cx, cy, r, mean_radial_residual)."""
    n = len(points)
    if n < 3: return None
    # Normal equations [Σx² Σxy Σx; Σxy Σy² Σy; Σx Σy n] · [a, b, c]^T
    Sx = Sy = Sxx = Syy = Sxy = Sb1 = Sb2 = Sb3 = 0.0
    for (x, y) in points:
        r2 = x * x + y * y
        Sx += x; Sy += y
        Sxx += x * x; Syy += y * y; Sxy += x * y
        Sb1 += x * r2
        Sb2 += y * r2
        Sb3 += r2
    A = [[Sxx, Sxy, Sx], [Sxy, Syy, Sy], [Sx, Sy, float(n)]]
    rhs = [Sb1, Sb2, Sb3]
    sol = solve_3x3(A, rhs)
    if sol is None: return None
    a, b, c = sol
    cx, cy = a / 2.0, b / 2.0
    r2 = c + cx * cx + cy * cy
    if r2 < 0: return None
    r = math.sqrt(r2)
    resid = sum(abs(math.hypot(x - cx, y - cy) - r) for (x, y) in points) / n
    return cx, cy, r, resid


def ransac_circle_2d(points, max_iter=400, inlier_tol_cm=0.25,
                     target_radius=None, radius_band=0.6, seed=42):
    """RANSAC 2D circle fit with radius-band rejection.

    points: list of (x, y) tuples in cm.
    max_iter: RANSAC sample count.
    inlier_tol_cm: max radial distance to be an inlier.
    target_radius: if given, only consider fits within ±radius_band of this.
    radius_band: width of acceptable radius window in cm.

    Returns dict with keys: center_xy (tuple), radius, n_inliers,
    n_total, residual_cm. Returns None if no acceptable fit.
    """
    n = len(points)
    if n < 4: return None
    rng = random.Random(seed)

    best = None  # (cx, cy, r, inlier_count)
    for _ in range(max_iter):
        i, j, k = rng.sample(range(n), 3)
        fit = fit_circle_3pt(points[i], points[j], points[k])
        if fit is None: continue
        cx, cy, r = fit
        if target_radius is not None:
            if abs(r - target_radius) > radius_band: continue
        # Count inliers
        cnt = 0
        for (x, y) in points:
            if abs(math.hypot(x - cx, y - cy) - r) < inlier_tol_cm:
                cnt += 1
        if best is None or cnt > best[3]:
            best = (cx, cy, r, cnt)

    if best is None: return None
    cx, cy, r, n_in = best
    if n_in < 4: return None

    # Refine with Kasa on inliers
    inliers = [(x, y) for (x, y) in points
               if abs(math.hypot(x - cx, y - cy) - r) < inlier_tol_cm]
    refined = fit_circle_kasa(inliers)
    if refined is not None:
        cx, cy, r, resid = refined
    else:
        resid = sum(abs(math.hypot(x - cx, y - cy) - r) for (x, y) in inliers) / len(inliers)

    return {
        "center_xy": (cx, cy),
        "radius": r,
        "n_inliers": len(inliers),
        "n_total": n,
        "residual_cm": resid,
    }


def fit_journal_on_throw(throw_obj, crank_axis_origin_xy, target_radius=5.5,
                          radius_band=1.5, top_radial_fraction=0.50):
    """Find the journal cylinder axis on a crank throw mesh.

    The crank rotation axis is +Z through crank_axis_origin_xy (we know
    this from the rig). The journal is a cylinder parallel to +Z, offset
    from the crank axis by ~target_radius. We find it by:

      1. Project vertices to XY (perpendicular to crank Z axis).
      2. Filter to high-radius vertices (top fraction by distance from
         crank axis) — excludes the central shaft cluster.
      3. RANSAC 2D circle fit with radius-band guard.

    Returns dict with center_xy, radius, residual_cm, z, world_centre.
    """
    pts_world = [throw_obj.matrix_world @ v.co for v in throw_obj.data.vertices]
    if not pts_world: return None
    cax, cay = crank_axis_origin_xy

    # Radial distance from crank axis (in XY)
    pts_xy = [(p.x, p.y) for p in pts_world]
    radii = [math.hypot(x - cax, y - cay) for (x, y) in pts_xy]

    # Top-fraction by radial distance
    n = len(pts_xy)
    k = max(8, int(n * top_radial_fraction))
    paired = sorted(zip(radii, pts_xy, pts_world), key=lambda t: -t[0])
    candidates_xy = [t[1] for t in paired[:k]]
    candidates_world = [t[2] for t in paired[:k]]

    fit = ransac_circle_2d(candidates_xy, target_radius=target_radius,
                            radius_band=radius_band)
    if fit is None: return None

    # Z of the journal = median Z of the inliers
    inlier_zs = sorted(
        p.z for (x, y), p in zip([(p.x, p.y) for p in candidates_world], candidates_world)
        if abs(math.hypot(x - fit["center_xy"][0], y - fit["center_xy"][1]) - fit["radius"]) < 0.3
    )
    if inlier_zs:
        z = inlier_zs[len(inlier_zs) // 2]
    else:
        z = sum(p.z for p in candidates_world) / len(candidates_world)

    cx, cy = fit["center_xy"]
    return {
        "center_xy": (cx, cy),
        "radius": fit["radius"],
        "residual_cm": fit["residual_cm"],
        "n_inliers": fit["n_inliers"],
        "n_candidates": fit["n_total"],
        "z": z,
        "world_centre": Vector((cx, cy, z)),
    }


def fit_ring_on_rod_end(rod_obj, long_axis_world, end="big",
                        end_fraction=0.18, target_radius=None, radius_band=1.5):
    """Find a ring (big-end or small-end) on a rod mesh.

    The ring's axis is approximately PERPENDICULAR to the rod's long axis
    (rings are perpendicular cross-sections at each end). We:

      1. Project all vertices onto the long axis → get a 1D projection.
      2. Take the slice 4%–18% inset from the tip (end='big' uses high
         projection, end='small' uses low). Skip the very-tip cap.
      3. Project those slice vertices onto the plane perpendicular to
         the long axis → 2D circle fit.
      4. The cylinder axis passes through the fitted centre in the
         perpendicular plane, parallel to the long axis. The ring centre
         in world = perpendicular-centre + axial_position * long_axis.

    Returns dict with center_world, radius, residual_cm.
    """
    long_axis = Vector(long_axis_world).normalized()
    pts_world = [rod_obj.matrix_world @ v.co for v in rod_obj.data.vertices]
    if not pts_world: return None

    # 1D projection along long axis
    projs = [p.dot(long_axis) for p in pts_world]
    lo_p, hi_p = min(projs), max(projs)
    extent = hi_p - lo_p
    if end == "big":
        band_lo = hi_p - extent * end_fraction
        band_hi = hi_p - extent * 0.04
    else:
        band_lo = lo_p + extent * 0.04
        band_hi = lo_p + extent * end_fraction
    slice_pts = [p for p, pr in zip(pts_world, projs) if band_lo <= pr <= band_hi]
    if len(slice_pts) < 6: return None

    # Build an orthonormal frame perpendicular to long_axis
    helper = Vector((0, 0, 1)) if abs(long_axis.z) < 0.9 else Vector((1, 0, 0))
    u_axis = (helper - long_axis * helper.dot(long_axis)).normalized()
    v_axis = long_axis.cross(u_axis).normalized()

    # Project slice points onto (u, v) plane
    proj2d = []
    for p in slice_pts:
        d = p - slice_pts[0]
        proj2d.append((d.dot(u_axis), d.dot(v_axis)))

    fit = ransac_circle_2d(proj2d, target_radius=target_radius,
                            radius_band=radius_band, inlier_tol_cm=0.25)
    if fit is None: return None

    # Recover world centre: anchor (slice_pts[0]) + cx*u + cy*v, then push
    # to mid-axial position of the slice (midpoint of band).
    cx, cy = fit["center_xy"]
    plane_origin = slice_pts[0]
    centre_in_plane = plane_origin + u_axis * cx + v_axis * cy
    # Move centre along long_axis to the median axial position of the slice
    slice_projs = [p.dot(long_axis) for p in slice_pts]
    median_proj = sorted(slice_projs)[len(slice_projs) // 2]
    axial_delta = median_proj - centre_in_plane.dot(long_axis)
    centre_world = centre_in_plane + long_axis * axial_delta

    return {
        "center_world": centre_world,
        "radius": fit["radius"],
        "residual_cm": fit["residual_cm"],
        "n_inliers": fit["n_inliers"],
        "n_total": fit["n_total"],
        "axial_position": median_proj,
    }


def detect_long_axis_world(obj):
    """Find the world-space long axis of a mesh via local bbox extents."""
    lo = Vector((float("inf"),) * 3); hi = Vector((-float("inf"),) * 3)
    for v in obj.data.vertices:
        lo = Vector(map(min, lo, v.co)); hi = Vector(map(max, hi, v.co))
    extents = hi - lo
    long_idx = max(range(3), key=lambda i: extents[i])
    sign = 1 if abs(hi[long_idx]) > abs(lo[long_idx]) else -1
    axis_local = Vector((0, 0, 0)); axis_local[long_idx] = float(sign)
    axis_world = (obj.matrix_world.to_3x3() @ axis_local).normalized()
    return axis_world, extents[long_idx]


print("[spatail_cylinder_fit] module loaded.")
