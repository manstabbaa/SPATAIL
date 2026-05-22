"""
spatail_verify_rig.py — headless rig diagnostics.

Run via:
    blender --background <rigged.blend> --python spatail_verify_rig.py -- <out.json>

Measures, per rigged kinematic group:

  small-end:
    - current rod origin (= pivot set by treatment) in world space
    - "ring centre" — vertices in the small-end SLICE (5-15% from the
       tip along the principal axis); their mean projection along the
       axis is the ring's axial position, their perpendicular centroid
       is the ring centre. We're isolating the ring (a band of vertices)
       and finding its geometric centre rather than the projection tip.
    - delta = ring_centre − current_origin (the offset we'd need to
       apply to land the pivot at the joint).

  big-end:
    - tip implied by Damped Track at rest (origin + long_axis × bbox_extent)
    - big-end ring centre (same algorithm at the other end)
    - delta_to_journal = ring_centre − journal_target_world

  kinematic:
    - effective rod length = ||tip_big − origin_small|| (what Damped Track sees)
    - centre-to-centre length = ||ring_big − ring_small||
    - piston-to-journal distance (the "length the rod has to span" in
       the assembly)

If centre-to-centre length ≈ piston-to-journal distance, the kinematic
chain is geometrically consistent. If the deltas are big, the pivots
need correction.
"""
import bpy, json, math, sys, os
from mathutils import Vector


def get_argv_after_double_dash():
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1:]
    return []


def slice_ring_centre(obj, axis_local, end="lo"):
    """Find the ring centre at one end of a rod-like mesh.

    Strategy: project all verts onto axis_local. Find the extreme value
    (lo or hi). Take vertices within 8-18% of the rod's total axial
    extent from that extreme — those are the END SLICE, dominated by
    ring geometry. Their projection mean is the ring's axial position;
    their perpendicular centroid (mean position projected onto plane
    perp to axis) is the ring's geometric centre.

    Returns (ring_centre_world, ring_axial_pos, tip_axial_pos).
    """
    pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
    if not pts:
        return None, None, None
    # Express axis in world frame (since vertices are world)
    axis_world = obj.matrix_world.to_3x3() @ axis_local
    axis_world = axis_world.normalized()
    projs = [(p.dot(axis_world), p) for p in pts]
    projs.sort(key=lambda x: x[0])
    lo_proj = projs[0][0]; hi_proj = projs[-1][0]
    extent = hi_proj - lo_proj
    if end == "lo":
        tip_proj = lo_proj
        # Slice from 8% to 18% inset from tip (skip the very-tip cap,
        # capture the ring body)
        lo_slice = lo_proj + extent * 0.04
        hi_slice = lo_proj + extent * 0.20
    else:
        tip_proj = hi_proj
        lo_slice = hi_proj - extent * 0.20
        hi_slice = hi_proj - extent * 0.04
    slice_pts = [p for proj, p in projs if lo_slice <= proj <= hi_slice]
    if not slice_pts:
        return None, None, tip_proj
    n = len(slice_pts)
    centre = Vector((
        sum(p.x for p in slice_pts) / n,
        sum(p.y for p in slice_pts) / n,
        sum(p.z for p in slice_pts) / n,
    ))
    ring_proj = sum(p.dot(axis_world) for p in slice_pts) / n
    return centre, ring_proj, tip_proj


def long_axis_local(obj):
    """Find rod's local long-axis direction (post-origin-shift)."""
    lo = Vector((float("inf"),)*3); hi = Vector((-float("inf"),)*3)
    for v in obj.data.vertices:
        lo = Vector(map(min, lo, v.co)); hi = Vector(map(max, hi, v.co))
    extents = hi - lo
    long_idx = max(range(3), key=lambda i: extents[i])
    # Which sign points to the FAR end (the side with greater absolute coord)?
    sign = 1 if abs(hi[long_idx]) > abs(lo[long_idx]) else -1
    axis = Vector((0, 0, 0)); axis[long_idx] = float(sign)
    return axis, long_idx, sign, extents[long_idx]


def diagnose(out_path=None):
    diagnostics = []
    scn = bpy.context.scene
    scn.frame_set(1)

    # For each piston with spatail_slot, find its child rod
    for piston in bpy.data.objects:
        if piston.type != "MESH": continue
        slot = piston.get("spatail_slot")
        if not slot: continue
        # Rod = piston's child with SPATAIL_rod_to_journal constraint
        rod = next((c for c in piston.children
                    if any(con.name.startswith("SPATAIL_rod_to_journal")
                           for con in c.constraints)), None)
        if rod is None: continue
        # Damped Track target = journal_target empty
        dt = next((con for con in rod.constraints
                   if con.name == "SPATAIL_rod_to_journal"), None)
        tgt = dt.target if dt else None
        if tgt is None: continue

        # Snapshot key world positions at frame 1
        rod_origin = rod.matrix_world.translation.copy()
        piston_centre = piston.matrix_world.translation.copy()
        journal_world = tgt.matrix_world.translation.copy()

        # Rod long axis (local frame). ax_local ALWAYS points toward big-end
        # (small-end is at the local origin after treatment's origin shift).
        ax_local, long_idx, sign, extent = long_axis_local(rod)

        # Slice both ends along the SAME axis. "lo" projection = closer to
        # origin = small-end. "hi" projection = far from origin = big-end.
        small_centre, small_proj, small_tip = slice_ring_centre(
            rod, ax_local, end="lo")
        big_centre, big_proj, big_tip = slice_ring_centre(
            rod, ax_local, end="hi")

        # Effective Damped-Track length (origin to tip along long axis)
        effective_length = extent  # local extent of the rod (since origin is
        # at one end, the other extreme is at distance = extent)

        # Centre-to-centre length (joint-to-joint)
        c2c_length = (big_centre - small_centre).length if (small_centre and big_centre) else None

        # Piston-to-journal distance at rest
        pj_dist = (piston_centre - journal_world).length

        diagnostics.append({
            "cyl": slot.get("cyl"),
            "bank": slot.get("bank"),
            "piston": piston.name,
            "rod": rod.name,
            "journal_target": tgt.name,
            "rod_origin_world": [round(c, 3) for c in rod_origin],
            "small_ring_centre_world":
                [round(c, 3) for c in small_centre] if small_centre else None,
            "big_ring_centre_world":
                [round(c, 3) for c in big_centre] if big_centre else None,
            "journal_target_world": [round(c, 3) for c in journal_world],
            "piston_centre_world":  [round(c, 3) for c in piston_centre],
            "long_axis_extent_cm": round(extent, 3),
            "effective_DT_length_cm": round(effective_length, 3),
            "centre_to_centre_length_cm": round(c2c_length, 3) if c2c_length else None,
            "piston_to_journal_cm": round(pj_dist, 3),
            # Deltas
            "delta_origin_vs_small_ring_cm":
                round((Vector(rod_origin) - Vector(small_centre)).length, 3)
                if small_centre else None,
            "delta_DTtip_vs_journal_cm": round(effective_length - pj_dist, 3),
            "delta_c2c_vs_PJ_cm":
                round((c2c_length - pj_dist), 3) if c2c_length else None,
        })

    diagnostics.sort(key=lambda d: (d["cyl"], d["bank"]))
    out = {
        "diagnostics_per_group": diagnostics,
        "summary": {
            "groups": len(diagnostics),
            "median_delta_origin_vs_small_ring_cm": _median(
                [d["delta_origin_vs_small_ring_cm"] for d in diagnostics
                 if d["delta_origin_vs_small_ring_cm"] is not None]),
            "median_delta_DTtip_vs_journal_cm": _median(
                [d["delta_DTtip_vs_journal_cm"] for d in diagnostics
                 if d["delta_DTtip_vs_journal_cm"] is not None]),
            "median_centre_to_centre_cm": _median(
                [d["centre_to_centre_length_cm"] for d in diagnostics
                 if d["centre_to_centre_length_cm"] is not None]),
            "median_effective_DT_length_cm": _median(
                [d["effective_DT_length_cm"] for d in diagnostics
                 if d["effective_DT_length_cm"] is not None]),
            "median_piston_to_journal_cm": _median(
                [d["piston_to_journal_cm"] for d in diagnostics
                 if d["piston_to_journal_cm"] is not None]),
        },
    }

    if out_path:
        with open(out_path, "w") as f:
            json.dump(out, f, indent=2)
        print(f"[verify_rig] -> {out_path}")
    print(json.dumps(out["summary"], indent=2))
    return out


def _median(vals):
    if not vals: return None
    s = sorted(vals)
    n = len(s)
    if n % 2: return round(s[n//2], 3)
    return round((s[n//2 - 1] + s[n//2]) / 2, 3)


if __name__ == "__main__":
    args = get_argv_after_double_dash()
    out = args[0] if args else r"C:/SPATAIL_MAX/assets_processed/treated/v10_engine/v10_engine.verify.json"
    diagnose(out)
