"""
spatail_reassemble.py — build the assembled engine from per-part
measurements + classification, ensuring pistons slide on lines through
the crank axis (kinematically correct slider-crank geometry).
"""
import bpy, json, math, os
from mathutils import Vector, Matrix


def _load_part_measurements(measurements_dir):
    """Return {partId: measurements_dict} from per-part JSONs."""
    index_path = os.path.join(measurements_dir, "_index.json")
    with open(index_path) as f:
        idx = json.load(f)
    out = {}
    for p in idx["parts"]:
        with open(p["json"]) as f:
            out[p["partId"]] = json.load(f)
    return out, idx


def _set_origin_world(obj, new_origin_world):
    """Shift mesh data so object origin lands at new_origin_world,
    keeping geometry in the same world position."""
    current = obj.matrix_world.translation.copy()
    delta_world = Vector(new_origin_world) - current
    delta_local = obj.matrix_world.inverted().to_3x3() @ delta_world
    obj.data.transform(Matrix.Translation(-delta_local))
    obj.matrix_world.translation = current + delta_world


def _orient_rod_via_joint_axis(rod, joint_axis_local, target_world_dir):
    """Rotate the rod's mesh data so its joint_axis_local direction (the
    actual joint-to-joint line measured by cylinder fit) maps to local +Y.
    No translation."""
    src = Vector(joint_axis_local).normalized()
    dst = Vector((0, 1, 0))
    if (src - dst).length < 1e-4: return
    axis = src.cross(dst)
    if axis.length < 1e-6:
        axis = Vector((1, 0, 0)) if abs(src.x) < 0.9 else Vector((0, 0, 1))
        angle = math.pi
    else:
        axis.normalize()
        dot = max(-1.0, min(1.0, src.dot(dst)))
        angle = math.acos(dot)
    rod.data.transform(Matrix.Rotation(angle, 4, axis))


def reassemble(treatment_json, classification_json, measurements_dir,
                out_blend, v_half_angle_deg=40.0, up_axis="+Y"):
    """Build the engine from scratch per blueprint."""
    with open(treatment_json) as f:
        T = json.load(f)
    with open(classification_json) as f:
        C = json.load(f)
    parts_measured, idx = _load_part_measurements(measurements_dir)

    asset_id = C.get("assetId", "asset")

    # ---- Phase A: anchor crank assembly at world origin -----------------
    # Crank axis = world +Z through origin. Throws will be placed at known Z
    # positions with journals at the correct angular phases.
    for nm in [o.name for o in bpy.data.objects
               if o.name == "crank_assembly" or o.name.startswith("journal_target_")]:
        bpy.data.objects.remove(bpy.data.objects[nm], do_unlink=True)
    crank_assy = bpy.data.objects.new("crank_assembly", None)
    bpy.context.scene.collection.objects.link(crank_assy)
    crank_assy.empty_display_type = "PLAIN_AXES"
    crank_assy.empty_display_size = 5.0
    crank_assy.location = (0, 0, 0)
    crank_assy.rotation_euler = (0, 0, 0)
    crank_axis = Vector((0, 0, 1))

    # ---- Phase B: clean up existing rigging ----------------------------
    for obj in [o for o in bpy.data.objects if o.type == "MESH"]:
        for con in list(obj.constraints):
            if con.name.startswith("SPATAIL_"): obj.constraints.remove(con)
        if obj.animation_data: obj.animation_data_clear()
        if obj.parent and obj.parent.type == "MESH":
            wm = obj.matrix_world.copy()
            obj.parent = None
            obj.matrix_world = wm

    # ---- Phase C: place crank throws -----------------------------------
    # Sort throws by their classification cylinderIndex (1..N).
    throws_meta = [p for p in C["parts"] if p.get("role") == "crank_throw"]
    throws_meta.sort(key=lambda p: p.get("cylinderIndex", 0))
    n_cyl = len(throws_meta)
    # Even Z spacing across the engine
    z_spacing = 12.0  # cm, matches the V10 OBJ — could be measured
    z_positions = [(n_cyl - 1) / 2.0 - i for i in range(n_cyl)]
    z_positions = [z * z_spacing for z in z_positions]
    # Phase angles for throws: typical V10 firing order spacing
    # We'll use the measured offset_local from each throw's journal measurement
    # to set the rotation angle of the throw about the crank axis.
    placement_log = []
    for throw_meta, z in zip(throws_meta, z_positions):
        throw = bpy.data.objects.get(throw_meta["name"])
        if throw is None: continue
        meas = parts_measured.get(throw_meta["name"])
        if not meas or not meas.get("journal"): continue

        # Reset throw transform to identity, then orient + place
        throw.parent = None
        throw.matrix_world = Matrix.Identity(4)
        # Set throw origin to its local journal centre (already done in mesh data?)
        # Place throw so journal axis is at (target_journal_xy, z)
        journal_local = Vector(meas["journal"]["center_local"])
        throw_radius = meas["journal"].get("throw_radius_cm", 5.5)

        # Compute desired journal XY in world based on phase (use cyl as phase index)
        cyl_idx = throw_meta.get("cylinderIndex", 1)
        # Even angular phases for visual; real firing order has specific phasing
        phase = (cyl_idx - 1) * (2 * math.pi / max(1, n_cyl)) + math.pi / 4
        journal_xy = Vector((math.cos(phase) * throw_radius,
                              math.sin(phase) * throw_radius, 0))
        # Set throw object position so that when journal_local is in world, it's at (journal_xy.x, journal_xy.y, z)
        # journal_world = throw.matrix_world @ journal_local
        # If throw at world position W with rotation R, journal_world = W + R @ journal_local
        # For simplicity: identity rotation, throw_W = (journal_xy.x - journal_local.x,
        #                                                journal_xy.y - journal_local.y,
        #                                                z - journal_local.z)
        throw_world = Vector((journal_xy.x - journal_local.x,
                                journal_xy.y - journal_local.y,
                                z - journal_local.z))
        throw.matrix_world = Matrix.Translation(throw_world)
        # Parent to crank_assembly preserving world
        wm = throw.matrix_world.copy()
        throw.parent = crank_assy
        throw.matrix_parent_inverse = crank_assy.matrix_world.inverted()
        throw.matrix_world = wm

        # Build journal_target empties (parented to throw)
        for bank in ("A", "B"):
            tgt_name = f"journal_target_{cyl_idx}_{bank}"
            tgt = bpy.data.objects.get(tgt_name)
            if tgt is None:
                tgt = bpy.data.objects.new(tgt_name, None)
                bpy.context.scene.collection.objects.link(tgt)
            tgt.empty_display_type = "SPHERE"
            tgt.empty_display_size = 0.4
            tgt.parent = None
            tgt.matrix_world = Matrix.Translation(Vector((journal_xy.x, journal_xy.y, z)))
            wm = tgt.matrix_world.copy()
            tgt.parent = throw
            tgt.matrix_parent_inverse = throw.matrix_world.inverted()
            tgt.matrix_world = wm

        placement_log.append({
            "cyl": cyl_idx,
            "throw_at_world": [round(c, 3) for c in throw_world],
            "journal_phase_rad": round(phase, 4),
            "journal_world": [round(c, 3) for c in (journal_xy.x, journal_xy.y, z)],
            "throw_radius_used": round(throw_radius, 3),
        })

    bpy.context.view_layer.update()

    # ---- Phase D: define bank-bore directions --------------------------
    # Pistons in bank A slide along bore_A; bank B along bore_B.
    # Both pass through crank axis (= world Z axis through origin).
    bank_half = math.radians(v_half_angle_deg)
    # +Y is "up" away from crank (engine block extends in +Y for typical V)
    bore_A = Vector((math.sin(-bank_half), math.cos(-bank_half), 0)).normalized()
    bore_B = Vector((math.sin(bank_half), math.cos(bank_half), 0)).normalized()

    # ---- Phase E: place pistons + rods + pins per kinematic group ------
    kinematic_groups = C.get("kinematicGroups", [])
    placement_per_cyl = []
    for group in kinematic_groups:
        cyl, bank = group["cylinderIndex"], group["bank"]
        bore_unit = bore_A if bank == "A" else bore_B
        # Find the journal_target for this cyl/bank
        tgt = bpy.data.objects.get(f"journal_target_{cyl}_{bank}")
        if tgt is None: continue
        journal_w = tgt.matrix_world.translation.copy()

        # Rod c2c length from rod's measurement
        rod_name = group.get("connectingRod")
        rod_meas = parts_measured.get(rod_name, {})
        c2c = float(rod_meas.get("c2c_length_cm") or 15.5)

        # Place piston EXACTLY where slider-crank says it should sit at rest.
        # Bore line: passes through (0, 0, journal_z) along bore_unit.
        # Piston at distance d from crank axis along bore.
        # Constraint: |piston − journal| = c2c.
        # Solving |d·bore − journal_xy|² = c2c² gives:
        #   d² − 2·d·(bore · journal_xy) + |journal_xy|² = c2c²
        #   d = (bore · journal_xy) ± √((bore · journal_xy)² + c2c² − r²)
        # Pick the "+" root (piston on the far side of the axis from the
        # crank centre — the extended-rod rest pose).
        journal_xy = Vector((journal_w.x, journal_w.y, 0))
        r_throw = journal_xy.length  # journal's distance from crank axis
        bore_dot_j = bore_unit.dot(journal_xy)
        disc = bore_dot_j * bore_dot_j + c2c * c2c - r_throw * r_throw
        if disc < 0:
            # Geometrically infeasible (rod can't span). Fall back.
            d = c2c
        else:
            d = bore_dot_j + math.sqrt(disc)
        piston_world = Vector((bore_unit.x * d, bore_unit.y * d, journal_w.z))

        piston_name = group["piston"]
        piston = bpy.data.objects.get(piston_name)
        if piston is None: continue
        piston.parent = None
        piston.matrix_world = Matrix.Translation(piston_world)
        # Stash slot for animator
        throw_radius = parts_measured.get(
            next((n for n, p in parts_measured.items()
                  if p.get("role") == "crank_throw"
                  and parts_measured[n].get("partId") in [
                      p["name"] for p in C["parts"]
                      if p.get("throwId") == group["throw"]]), ""),
            {}).get("journal", {}).get("throw_radius_cm", 5.9)
        piston["spatail_slot"] = {
            "cyl": cyl, "bank": bank,
            "boreAxisUnit": list(bore_unit),
            "restPistonWorld": list(piston_world),
            "journalWorld": list(journal_w),
            "axisOriginWorld": [0.0, 0.0, journal_w.z],
            "conrodLength_cm": c2c,
            "throwRadius_cm": float(throw_radius),
        }

        # Place rod: parent to piston, origin at small-ring (per measurement)
        rod = bpy.data.objects.get(rod_name)
        if rod is None: continue
        rod.parent = None
        rod_meas_obj = parts_measured.get(rod_name, {})
        rings = rod_meas_obj.get("joint_rings", {})
        if rings and rings.get("small"):
            small_local = rings["small"]["center_local"]
            joint_axis = rod_meas_obj.get("joint_axis_local", [0, 1, 0])
            # Shift mesh data so small ring is at local origin
            _set_origin_world(rod, rod.matrix_world @ Vector(small_local))
            # Rotate mesh so joint_axis_local → local +Y
            _orient_rod_via_joint_axis(rod, joint_axis, None)
        # Place rod at piston world position
        rod.matrix_world = Matrix.Translation(piston_world)
        wm = rod.matrix_world.copy()
        rod.parent = piston
        rod.matrix_parent_inverse = piston.matrix_world.inverted()
        rod.matrix_world = wm
        # Damped Track aim at journal_target
        for con in list(rod.constraints):
            if con.name.startswith("SPATAIL_"): rod.constraints.remove(con)
        dt = rod.constraints.new("DAMPED_TRACK")
        dt.name = "SPATAIL_rod_to_journal"
        dt.target = tgt
        dt.track_axis = "TRACK_Y"

        # Pin (optional)
        pin_name = group.get("wristPin")
        if pin_name:
            pin = bpy.data.objects.get(pin_name)
            if pin:
                pin.parent = None
                pin.matrix_world = Matrix.Translation(piston_world)
                wm = pin.matrix_world.copy()
                pin.parent = piston
                pin.matrix_parent_inverse = piston.matrix_world.inverted()
                pin.matrix_world = wm

        placement_per_cyl.append({
            "cyl": cyl, "bank": bank,
            "bore_unit": [round(c, 3) for c in bore_unit],
            "piston_at": [round(c, 3) for c in piston_world],
            "journal_at": [round(c, 3) for c in journal_w],
            "c2c_used_cm": round(c2c, 3),
            "piston_to_journal_cm": round((piston_world - journal_w).length, 3),
        })

    bpy.context.view_layer.update()
    bpy.ops.wm.save_as_mainfile(filepath=out_blend)

    print(f"[reassemble] saved → {out_blend}")
    return {
        "throws_placed": placement_log,
        "cyl_groups_placed": placement_per_cyl,
        "v_half_angle_deg": v_half_angle_deg,
        "bore_A": list(bore_A),
        "bore_B": list(bore_B),
        "saved": out_blend,
    }


print("[spatail_reassemble] module loaded.")
