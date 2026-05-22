"""
spatail_calibrate_assembly.py — apply the verify measurements to fix the rig.

Run via:
    blender --background <rigged.blend> --python spatail_calibrate_assembly.py -- <out.blend>

What it does, per cylinder:

  1. Find the rod's REAL small-end ring centre (slice 4-18% from the
     small-end tip along the long axis, take the centroid). The rod
     was previously pivoted at the small-end TIP; we move the pivot
     to the ring CENTRE by shifting mesh data along the long axis.

  2. Find the rod's REAL big-end ring centre the same way.

  3. Compute the rod's TRUE centre-to-centre length =
     ||big_ring_centre − small_ring_centre||.

  4. Move the piston along its bore axis so piston-to-journal distance
     equals the true centre-to-centre length. (The OBJ's authored
     piston positions are ~1cm too far from the journals to fit the
     real rod length.)

  5. Update each piston's `spatail_slot.restPistonWorld` to the new
     position so the animator's slider-crank uses the corrected rest.

  6. Re-run animator from the corrected rest pose.

  7. Save the calibrated .blend.
"""
import bpy, json, math, sys, os
from mathutils import Vector, Matrix


def get_argv_after_double_dash():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def long_axis_local(obj):
    lo = Vector((float("inf"),)*3); hi = Vector((-float("inf"),)*3)
    for v in obj.data.vertices:
        lo = Vector(map(min, lo, v.co)); hi = Vector(map(max, hi, v.co))
    extents = hi - lo
    long_idx = max(range(3), key=lambda i: extents[i])
    # axis points to big-end (the side with greater absolute coord)
    sign = 1 if abs(hi[long_idx]) > abs(lo[long_idx]) else -1
    axis = Vector((0, 0, 0)); axis[long_idx] = float(sign)
    return axis, long_idx, sign, extents[long_idx], lo, hi


def slice_ring_centre_world(obj, axis_local, end):
    """Slice 4-18% of vertices from one end along the local long axis,
    return their world centroid. end ∈ {'small','big'}; 'small' = LOW
    projection along axis_local (which points to big end); 'big' = HIGH."""
    pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
    if not pts: return None
    axis_world = (obj.matrix_world.to_3x3() @ axis_local).normalized()
    projs = [(p.dot(axis_world), p) for p in pts]
    projs.sort(key=lambda x: x[0])
    lo_proj = projs[0][0]; hi_proj = projs[-1][0]
    extent = hi_proj - lo_proj
    if end == "small":
        lo_band = lo_proj + extent * 0.04
        hi_band = lo_proj + extent * 0.18
    else:
        lo_band = hi_proj - extent * 0.18
        hi_band = hi_proj - extent * 0.04
    slice_pts = [p for proj, p in projs if lo_band <= proj <= hi_band]
    if not slice_pts: return None
    n = len(slice_pts)
    return Vector((sum(p.x for p in slice_pts)/n,
                   sum(p.y for p in slice_pts)/n,
                   sum(p.z for p in slice_pts)/n))


def shift_mesh_data(obj, delta_local):
    """Translate the mesh DATA by delta_local (so the geometry moves
    relative to object origin). Keep obj.matrix_world unchanged."""
    obj.data.transform(Matrix.Translation(delta_local))


def align_rod_local_axis_to_joints(rod_obj, small_ring_centre_w, big_ring_centre_w,
                                    target_local_axis="Y"):
    """Rotate the rod's MESH DATA so its local target_local_axis (default
    +Y) aligns with the joint-to-joint line (small_ring_centre →
    big_ring_centre, in world). The rod's origin (assumed at small ring
    centre) doesn't move; only the mesh data rotates around it.

    Why: for asymmetric rods (big ring offset from PCA principal axis),
    the PCA long axis isn't the joint line. Damped Track aims a local
    axis at the journal target — so we force the local axis to BE the
    joint line by rotating the mesh.
    """
    # Desired direction in world: from origin to big_ring_centre
    desired_w = (big_ring_centre_w - small_ring_centre_w)
    if desired_w.length < 1e-6: return
    desired_w.normalize()

    # Current direction of local +Y (or other) in WORLD
    local_axis_v = Vector((0, 0, 0))
    idx = {"X": 0, "Y": 1, "Z": 2}.get(target_local_axis.upper(), 1)
    local_axis_v[idx] = 1.0
    current_w = (rod_obj.matrix_world.to_3x3() @ local_axis_v).normalized()

    # Rotation that takes current_w to desired_w
    if (current_w - desired_w).length < 1e-6: return
    axis_of_rot = current_w.cross(desired_w)
    if axis_of_rot.length < 1e-6:
        # 180° flip — rotate around any perpendicular axis
        axis_of_rot = Vector((1, 0, 0)) if abs(current_w.x) < 0.9 else Vector((0, 1, 0))
        axis_of_rot = (axis_of_rot - current_w * axis_of_rot.dot(current_w)).normalized()
        angle = math.pi
    else:
        axis_of_rot.normalize()
        dot = max(-1.0, min(1.0, current_w.dot(desired_w)))
        angle = math.acos(dot)

    # Express rotation in the rod's LOCAL frame, then apply to mesh data
    rot_world = Matrix.Rotation(angle, 4, axis_of_rot)
    # The world transform on the mesh would be: world @ rot @ local
    # We want to rotate the mesh data in place around the object origin.
    # Convert the world rotation to a local-space rotation:
    inv = rod_obj.matrix_world.to_3x3().inverted()
    axis_local = inv @ axis_of_rot
    rot_local = Matrix.Rotation(angle, 4, axis_local.normalized())
    rod_obj.data.transform(rot_local)


def calibrate(out_blend_path):
    scn = bpy.context.scene
    scn.frame_set(1)

    per_cyl = []
    for piston in list(bpy.data.objects):
        if piston.type != "MESH": continue
        slot = piston.get("spatail_slot")
        if not slot: continue

        # Find rod (child of piston with SPATAIL_rod_to_journal constraint)
        rod = next((c for c in piston.children
                    if any(con.name.startswith("SPATAIL_rod_to_journal")
                           for con in c.constraints)), None)
        if rod is None: continue
        dt = next((c for c in rod.constraints
                   if c.name.startswith("SPATAIL_rod_to_journal")), None)
        tgt = dt.target if dt else None
        if tgt is None: continue

        cyl = slot.get("cyl"); bank = slot.get("bank")

        # ---- 1) Ring centres in WORLD coords (pre-calibration) ----------
        ax_local, long_idx, sign, extent, lo_local, hi_local = long_axis_local(rod)
        small_centre_w = slice_ring_centre_world(rod, ax_local, end="small")
        big_centre_w = slice_ring_centre_world(rod, ax_local, end="big")
        if small_centre_w is None or big_centre_w is None:
            continue

        # Current rod origin in world
        rod_origin_w = rod.matrix_world.translation.copy()

        # Inset of small-ring-centre from rod origin (along world long axis)
        ax_world = (rod.matrix_world.to_3x3() @ ax_local).normalized()
        inset_small = (small_centre_w - rod_origin_w).dot(ax_world)
        # inset_small > 0 means small-ring-centre is OFFSET from origin
        # by `inset_small` in the +long-axis direction. We need to move the
        # origin TO the small-ring-centre — equivalently, shift mesh data
        # the OPPOSITE way (so mesh moves "back" by inset_small relative
        # to the new origin).

        # ---- 2) Shift mesh data so small-ring-centre is at local origin -
        delta_local = -ax_local * inset_small
        shift_mesh_data(rod, delta_local)
        rod.matrix_world.translation = small_centre_w.copy()
        bpy.context.view_layer.update()

        # ---- 2.5) ROTATE mesh data so joint-to-joint line becomes local +Y
        # GUARDED: skip rotation if it would flip the rod (angle > 90°);
        # an over-90° rotation indicates the detected "big end" is actually
        # the small end, in which case rotation makes things worse.
        big_centre_w2 = slice_ring_centre_world(rod, ax_local, end="big")
        if big_centre_w2 is None: continue
        new_origin_w = rod.matrix_world.translation.copy()
        desired_w = (big_centre_w2 - new_origin_w)
        if desired_w.length > 1e-3:
            desired_w.normalize()
            current_local_Y_world = (rod.matrix_world.to_3x3() @ Vector((0,1,0))).normalized()
            cosang = current_local_Y_world.dot(desired_w)
            if cosang > 0:  # less than 90° → safe to align
                align_rod_local_axis_to_joints(
                    rod, new_origin_w, big_centre_w2, target_local_axis="Y")
                for con in rod.constraints:
                    if con.name.startswith("SPATAIL_rod_to_journal"):
                        con.track_axis = "TRACK_Y"
                bpy.context.view_layer.update()
        # Centre-to-centre length post any rotation
        big_centre_w3 = slice_ring_centre_world(rod, Vector((0,1.0,0)), end="big")
        if big_centre_w3 is None:
            big_centre_w3 = big_centre_w2
        c2c_length = (big_centre_w3 - new_origin_w).length
        # Sanity check: c2c should be at least 50% of rod bbox extent.
        # If it's tiny, fall back to using the original big_centre_w2.
        if c2c_length < extent * 0.5:
            c2c_length = (big_centre_w2 - new_origin_w).length

        # ---- 4) Move piston so piston_to_journal = c2c_length ----------
        journal_w = tgt.matrix_world.translation.copy()
        bore_unit = Vector(list(slot.get("boreAxisUnit", (0, 1, 0)))).normalized()
        # Position piston at journal + bore_unit × c2c_length
        piston_new = journal_w + bore_unit * c2c_length
        # Lock the Z of piston to its original Z (so it stays in its cyl plane)
        piston_old = piston.matrix_world.translation.copy()
        piston_new.z = piston_old.z
        # Recompute the actual centre-to-centre distance achievable in
        # that XY plane (since we constrained Z):
        actual_dist = (piston_new - journal_w).length
        piston.matrix_world.translation = piston_new
        bpy.context.view_layer.update()

        # The rod is parented to the piston; its world position follows.
        # We need to RE-set the rod's location relative to piston so its
        # origin (small-ring-centre) is at the new piston position.
        rod.parent = None
        rod.matrix_world.translation = piston_new
        # reparent
        wm = rod.matrix_world.copy()
        rod.parent = piston
        rod.matrix_parent_inverse = piston.matrix_world.inverted()
        rod.matrix_world = wm
        bpy.context.view_layer.update()

        # ---- 5) Update spatail_slot.restPistonWorld for animator -------
        new_slot = dict(slot)
        new_slot["restPistonWorld"] = list(piston_new)
        new_slot["conrodLength_cm"] = float(c2c_length)
        new_slot["centreToCentreLength_cm"] = float(c2c_length)
        piston["spatail_slot"] = new_slot

        per_cyl.append({
            "cyl": cyl, "bank": bank,
            "small_ring_inset_applied_cm": round(inset_small, 3),
            "centre_to_centre_cm": round(c2c_length, 3),
            "piston_old": [round(c, 3) for c in piston_old],
            "piston_new": [round(c, 3) for c in piston_new],
            "piston_move_cm": round((piston_new - piston_old).length, 3),
        })

    # Re-run animator with corrected rest poses
    anim_path = r"C:/SPATAIL_MAX/pipeline/blender/spatail_animate_four_stroke.py"
    if os.path.exists(anim_path):
        ns = {"__name__": "__animator__"}
        exec(open(anim_path).read(), ns)
        ns["animate_four_stroke"](cycle_frames=120, rotations_per_cycle=2, sample_step=2)

    bpy.ops.wm.save_as_mainfile(filepath=out_blend_path)
    print(f"[calibrate_assembly] saved -> {out_blend_path}")
    print(f"[calibrate_assembly] calibrated {len(per_cyl)} cylinders")
    print(f"[calibrate_assembly] per-cyl:")
    for c in per_cyl:
        print(f"  cyl {c['cyl']}{c['bank']}: inset={c['small_ring_inset_applied_cm']}cm "
              f"c2c={c['centre_to_centre_cm']}cm piston_moved={c['piston_move_cm']}cm")
    return per_cyl


if __name__ == "__main__":
    args = get_argv_after_double_dash()
    out = args[0] if args else r"C:/SPATAIL_MAX/assets_authoring/v10_engine_calibrated.blend"
    calibrate(out)
