"""
spatail_rig_four_stroke.py — v3-architecture rigging with FIXED PIVOTS.

THE KEY FIX FROM EARLIER VERSIONS:
  Damped Track rotates an object around its origin. Imported OBJ meshes
  have their origins wherever the exporter dropped them — usually a bbox
  corner or world (0,0,0), not the physically-correct pivot. When the
  v3 animator added a Damped Track to a rod whose origin was at a corner,
  the rod's small-end DRIFTED away from the wrist pin as the rod rotated.
  The fix here: set each rod's origin to its SMALL-END (the wrist-pin
  side, identified via PCA). Then Damped Track rotates the rod around
  the joint that's pinned to the piston — physically correct.

RIG TOPOLOGY (v3-style):
  engine_root
  ├─ crank_assembly (empty at axis_origin)
  │  └─ 5 crank throws (reparented, world preserved)
  ├─ 10 pistons (kept at OBJ-authored positions, location keyframed by animator)
  │  └─ 10 rods (parented to piston, origin moved to small-end)
  │      └─ Damped Track aiming at journal_target_<idx>_<bank>
  └─ 10 journal_target_<idx>_<bank> empties (parented to each throw at journal centre)

The animator keyframes each piston's location along its bore axis using
slider-crank projection (journal_now − journal_rest) · bore_unit.

USAGE:
    exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_rig_four_stroke.py").read())
    rig_four_stroke(r".../v10_engine.measurements.four_stroke_motion.json")
"""

import bpy, json, math
from mathutils import Vector, Matrix


def _bbox_world(obj):
    lo = Vector((float("inf"),)*3); hi = Vector((-float("inf"),)*3)
    for c in obj.bound_box:
        p = obj.matrix_world @ Vector(c)
        lo = Vector(map(min, lo, p)); hi = Vector(map(max, hi, p))
    return lo, hi


def _set_origin_world(obj, new_origin_world):
    """Shift mesh data so the object origin lands at new_origin_world
    while geometry stays put in world space."""
    current = obj.matrix_world.translation.copy()
    delta_world = Vector(new_origin_world) - current
    delta_local = obj.matrix_world.inverted().to_3x3() @ delta_world
    obj.data.transform(Matrix.Translation(-delta_local))
    obj.matrix_world.translation = current + delta_world


def rig_four_stroke(measurements_json):
    """Build v3-style rig from measurements."""
    with open(measurements_json) as f:
        M = json.load(f)

    axis_origin = Vector(M["crank"]["axisOriginWorld"])
    cylinders = M["cylinders"]
    throws_by_id = {t["throwId"]: t for t in M["throws"] if "journalCentreWorld" in t}
    if not cylinders:
        print("[rig] no cylinders — abort"); return

    root = bpy.data.objects.get("engine_root")
    if root is None:
        print("[rig] no engine_root — abort"); return
    coll = bpy.data.collections.get("V10_Engine") or root.users_collection[0]

    # ---- crank_assembly empty at the rotation axis -------------------
    crank_assy = bpy.data.objects.get("crank_assembly")
    if crank_assy is None:
        crank_assy = bpy.data.objects.new("crank_assembly", None)
        coll.objects.link(crank_assy)
    crank_assy.empty_display_size = 3.0
    crank_assy.animation_data_clear()
    # Unparent throws first so we can reset crank_assy cleanly
    for tid in throws_by_id:
        t_obj = bpy.data.objects.get(tid)
        if t_obj and t_obj.parent and t_obj.parent != crank_assy:
            wm = t_obj.matrix_world.copy()
            t_obj.parent = None
            t_obj.matrix_world = wm
    if crank_assy.parent is not root:
        crank_assy.parent = root
    world_to_engine = root.matrix_world.inverted()
    crank_assy.location = world_to_engine @ Vector((axis_origin.x, axis_origin.y, 0))
    crank_assy.rotation_euler = (0, 0, 0)
    bpy.context.view_layer.update()

    # ---- Reparent throws under crank_assy (world preserved) ---------
    def reparent(child, new_parent):
        if child.parent is new_parent: return
        wm = child.matrix_world.copy()
        child.parent = new_parent
        child.matrix_parent_inverse = new_parent.matrix_world.inverted()
        child.matrix_world = wm

    for tid in throws_by_id:
        t_obj = bpy.data.objects.get(tid)
        if t_obj: reparent(t_obj, crank_assy)

    # ---- Per cylinder: piston → rod (with small-end origin) -------
    rigged = 0
    for c in cylinders:
        idx, bank = c["cylinderIndex"], c["bank"]
        rod = bpy.data.objects.get(c["rodId"])
        piston = bpy.data.objects.get(c["pistonId"])
        throw = bpy.data.objects.get(c["throwId"])
        if not (rod and piston and throw): continue
        journal = Vector(c["restJournalPosition"])
        small_end = Vector(c["restRodSmallEnd"])

        # 1) journal_target empty parented to throw at journal centre
        tgt_name = f"journal_target_{idx}_{bank}"
        tgt = bpy.data.objects.get(tgt_name)
        if tgt is None:
            tgt = bpy.data.objects.new(tgt_name, None)
            coll.objects.link(tgt)
        tgt.empty_display_type = "SPHERE"
        tgt.empty_display_size = 0.5
        tgt.parent = None
        tgt.matrix_world = Matrix.Translation(journal)
        wm = tgt.matrix_world.copy()
        tgt.parent = throw
        tgt.matrix_parent_inverse = throw.matrix_world.inverted()
        tgt.matrix_world = wm

        # 2) Piston: keep authored position. Set origin to bbox centroid
        # so future location keyframes pivot symmetrically (no functional
        # rotation on pistons, but cleaner numerics for the animator).
        if piston.parent is not None:
            wm = piston.matrix_world.copy()
            piston.parent = None; piston.matrix_world = wm
        piston.animation_data_clear()
        lo_p, hi_p = _bbox_world(piston)
        piston_centroid = (lo_p + hi_p) * 0.5
        _set_origin_world(piston, piston_centroid)

        # 3) Rod: CRITICAL — set origin to PCA small-end. This is the
        # wrist-pin pivot. Damped Track rotates around the object origin,
        # so without this the rod's small-end drifts away from the
        # piston as the constraint aims the rod. Setting origin to the
        # small-end pins that joint to the piston correctly.
        # Clear old constraints + reparent
        for con in list(rod.constraints):
            if con.name.startswith("SPATAIL_"):
                rod.constraints.remove(con)
        if rod.parent is not None:
            wm = rod.matrix_world.copy()
            rod.parent = None; rod.matrix_world = wm
        rod.animation_data_clear()
        _set_origin_world(rod, small_end)
        # Now parent rod under piston (small-end will translate with piston)
        wm = rod.matrix_world.copy()
        rod.parent = piston
        rod.matrix_parent_inverse = piston.matrix_world.inverted()
        rod.matrix_world = wm

        # 4) Determine which local axis of the rod points toward the
        # big-end (so we know which TRACK axis Damped Track should use).
        lo = Vector((float("inf"),)*3); hi = Vector((-float("inf"),)*3)
        for v in rod.data.vertices:
            lo = Vector(map(min, lo, v.co)); hi = Vector(map(max, hi, v.co))
        extents = hi - lo
        long_idx = max(range(3), key=lambda i: extents[i])
        # Big-end direction in local space: whichever extent has larger
        # absolute value along the long axis is "far end" (= big end,
        # since origin is now at small end)
        far_sign = 1 if abs(hi[long_idx]) > abs(lo[long_idx]) else -1
        TRACK = {(0, +1): "TRACK_X", (0, -1): "TRACK_NEGATIVE_X",
                 (1, +1): "TRACK_Y", (1, -1): "TRACK_NEGATIVE_Y",
                 (2, +1): "TRACK_Z", (2, -1): "TRACK_NEGATIVE_Z"}
        track_axis = TRACK[(long_idx, far_sign)]

        # 5) Damped Track on rod aimed at journal_target — rotates around
        # rod's (now small-end) origin to point its long axis at the
        # crank journal.
        dt = rod.constraints.new("DAMPED_TRACK")
        dt.name = "SPATAIL_rod_to_journal"
        dt.target = tgt
        dt.track_axis = track_axis

        # Stash per-cyl data for the animator
        piston["spatail_slot"] = {
            "cyl": idx, "bank": bank,
            "boreAxisUnit": list(c["boreAxisUnit"]),
            "restPistonWorld": list(piston_centroid),
            "journalWorld": list(journal),
            "axisOriginWorld": list(axis_origin),
            "conrodLength_cm": c["conrodLength_cm"],
        }
        rigged += 1

    print(f"[rig] {rigged} cylinders rigged (v3 + small-end pivots)")
    return {"cylindersRigged": rigged, "axisOrigin": list(axis_origin)}


print("[spatail_rig_four_stroke] module loaded.")
