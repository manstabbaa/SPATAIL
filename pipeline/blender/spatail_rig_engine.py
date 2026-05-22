"""
spatail_rig_engine.py — engine kinematic rigger.

See skills/spatail-rig-engine/SKILL.md for design rationale.

INPUT:
  - <asset>.treatment.json   (pivots, shape classes, bbox info)
  - <asset>.classification.json  (roles, kinematic groups, crank axis)
  - The Blender scene with treated parts present.

OUTPUT (mutates scene):
  - crank_assembly empty at the crank axis.
  - Throws reparented under crank_assembly.
  - journal_target_<idx>_<bank> empties parented to each throw.
  - Each rod moved + parented to its assigned piston.
  - Each rod gets a Damped Track constraint aimed at its journal_target.
  - Each piston tagged with `spatail_slot` custom prop for the animator.

NO geometry inspection. Reads JSON, manipulates objects, writes
constraints + properties. That's it.
"""

import bpy, json, math, os
from mathutils import Vector, Matrix


def _vec(a):
    return Vector(a) if a is not None else Vector((0, 0, 0))


def _local_bbox(obj):
    """Mesh-data bbox in local space."""
    lo = Vector((float("inf"),)*3); hi = Vector((-float("inf"),)*3)
    for v in obj.data.vertices:
        lo = Vector(map(min, lo, v.co)); hi = Vector(map(max, hi, v.co))
    return lo, hi


def _world_bbox(obj):
    lo = Vector((float("inf"),)*3); hi = Vector((-float("inf"),)*3)
    for c in obj.bound_box:
        p = obj.matrix_world @ Vector(c)
        lo = Vector(map(min, lo, p)); hi = Vector(map(max, hi, p))
    return lo, hi


def _refine_journal_centre(throw_obj, crank_axis_origin, crank_axis_dir):
    """For a crank throw mesh, find the actual journal cylinder by
    radial clustering. The throw's bbox centre lies between counter-
    weight and journal post — biased — so we can't use it as the
    journal. The journal post is the cluster of vertices furthest from
    the crank axis (it sticks out the most).

    Algorithm:
      1. Project all vertices onto plane perpendicular to crank axis.
      2. Compute radial distance from crank axis for each vertex.
      3. Take top 35% by radial distance (= journal + counterweight tips).
      4. Split into two angular halves around the axis.
      5. Pick the half with SMALLER XY spread — the journal is a tight
         cylindrical post, the counterweight is a wider slab.
      6. Return that half's vertex mean as the journal centre.
    """
    if not throw_obj or throw_obj.type != "MESH":
        return None
    axis_origin = Vector(crank_axis_origin)
    axis_dir = Vector(crank_axis_dir).normalized()
    pts_world = [throw_obj.matrix_world @ v.co for v in throw_obj.data.vertices]
    if len(pts_world) < 8:
        return None

    # Radial distance from crank axis (perp component magnitude)
    def radial(p):
        d = p - axis_origin
        d_perp = d - axis_dir * d.dot(axis_dir)
        return d_perp.length

    radii = [radial(p) for p in pts_world]
    rmax = max(radii)
    cutoff = rmax * 0.65
    far = [(p, r) for p, r in zip(pts_world, radii) if r >= cutoff]
    if not far:
        return None

    # Angular position around crank axis. Use a perp reference frame.
    world_x = Vector((1.0, 0.0, 0.0))
    ref_x = (world_x - axis_dir * world_x.dot(axis_dir))
    if ref_x.length < 1e-4:
        ref_x = Vector((0.0, 1.0, 0.0))
        ref_x = (ref_x - axis_dir * ref_x.dot(axis_dir))
    ref_x = ref_x.normalized()
    ref_y = axis_dir.cross(ref_x).normalized()

    def angle(p):
        d = p - axis_origin
        return math.atan2(d.dot(ref_y), d.dot(ref_x))

    angles = [angle(p) for p, _ in far]
    med = sorted(angles)[len(angles) // 2]

    g1, g2 = [], []
    for (p, _), a in zip(far, angles):
        delta = abs(((a - med + math.pi) % (2 * math.pi)) - math.pi)
        (g1 if delta < math.pi / 2 else g2).append(p)

    def angular_spread(group):
        """Spread of group's angles around the crank axis. Journal is a
        tight angular cluster (narrow cylinder post); counterweight spans
        a wider angular arc (slab shape). Smaller spread = journal."""
        if not group: return float("inf")
        angles = []
        for p in group:
            d = p - axis_origin
            angles.append(math.atan2(d.dot(ref_y), d.dot(ref_x)))
        if not angles: return float("inf")
        # Stddev of angles (modular)
        amean = sum(angles) / len(angles)
        var = sum(((((a - amean + math.pi) % (2 * math.pi)) - math.pi) ** 2)
                  for a in angles) / len(angles)
        return math.sqrt(var)

    journal_pts = g1 if angular_spread(g1) <= angular_spread(g2) else g2
    if not journal_pts:
        return None
    n = len(journal_pts)
    return Vector((
        sum(p.x for p in journal_pts) / n,
        sum(p.y for p in journal_pts) / n,
        sum(p.z for p in journal_pts) / n,
    ))


def _detect_track_axis(rod):
    """Find which local axis is the rod's long axis (post-origin-shift)
    AND which direction (+ or -) points toward the BIG end.

    Treatment set origin at the SMALL end, so the big end lies at the
    far extreme of the longest local axis. Returns ('TRACK_X' / 'TRACK_Y'
    / 'TRACK_Z' or their NEGATIVE_ counterparts) for the Damped Track
    constraint.
    """
    lo, hi = _local_bbox(rod)
    extents = hi - lo
    long_idx = max(range(3), key=lambda i: extents[i])
    # Which sign of the long axis is the big end?
    if abs(hi[long_idx]) >= abs(lo[long_idx]):
        sign = +1
    else:
        sign = -1
    AXIS_NAMES = ["X", "Y", "Z"]
    name = AXIS_NAMES[long_idx]
    return f"TRACK_{name}" if sign > 0 else f"TRACK_NEGATIVE_{name}"


def rig_engine(treatment_json, classification_json):
    with open(treatment_json) as f:
        T = json.load(f)
    with open(classification_json) as f:
        C = json.load(f)

    asset_id = C.get("assetId", T.get("assetId", "asset"))
    crank_axis_dir = _vec(C["crank"]["axis_direction"])
    crank_axis_origin = _vec(C["crank"]["axis_origin"])

    # ---- engine_root collection scaffold ---------------------------------
    coll_name = f"{asset_id}_rigged"
    coll = bpy.data.collections.get(coll_name)
    if coll is None:
        coll = bpy.data.collections.new(coll_name)
        bpy.context.scene.collection.children.link(coll)

    # ---- crank_assembly empty at the rotation axis -----------------------
    crank_assy = bpy.data.objects.get("crank_assembly")
    if crank_assy is None:
        crank_assy = bpy.data.objects.new("crank_assembly", None)
        coll.objects.link(crank_assy)
    crank_assy.empty_display_type = "PLAIN_AXES"
    crank_assy.empty_display_size = 5.0
    crank_assy.animation_data_clear()
    crank_assy.location = crank_axis_origin
    # Align crank_assy's local +Z to the crank axis direction so
    # rotation_euler[2] is rotation about the crank.
    z_local = Vector((0, 0, 1))
    crank_axis_dir_n = crank_axis_dir.normalized()
    if (crank_axis_dir_n - z_local).length > 1e-3:
        # Build a rotation that takes +Z to crank_axis_dir
        rot_quat = z_local.rotation_difference(crank_axis_dir_n)
        crank_assy.rotation_euler = rot_quat.to_euler()
    else:
        crank_assy.rotation_euler = (0, 0, 0)
    bpy.context.view_layer.update()

    # ---- helper: reparent preserving world transform --------------------
    def reparent(child, new_parent):
        if child.parent is new_parent: return
        wm = child.matrix_world.copy()
        child.parent = new_parent
        child.matrix_parent_inverse = new_parent.matrix_world.inverted()
        child.matrix_world = wm

    # ---- Pass 1: refine journal centres + reparent throws ----------------
    # Classifier exposed throw bbox centres as journalCentre, but bbox
    # centre is biased between counterweight + journal. Refine to the
    # actual journal post (high-radius angular cluster) BEFORE reparenting
    # — refinement uses world-space mesh data, which is cleaner before the
    # throw inherits any parent transform.
    throw_name_by_id = {}
    refined_journals = {}  # throwId → Vector(world journal centre)
    for part in C["parts"]:
        if part.get("role") != "crank_throw": continue
        t_obj = bpy.data.objects.get(part["name"])
        if not t_obj: continue
        throw_name_by_id[part["throwId"]] = part["name"]
        # Run refinement on the (still-unparented) throw mesh
        refined = _refine_journal_centre(t_obj, crank_axis_origin, crank_axis_dir_n)
        if refined is not None:
            refined_journals[part["throwId"]] = refined
        reparent(t_obj, crank_assy)
    print(f"[rig_engine] refined {len(refined_journals)} journal centres "
          f"from mesh geometry (vs bbox-centre approximations)")

    # ---- Pass 2: per kinematic group, assemble + constrain --------------
    rigged = 0
    missing = []
    for group in C["kinematicGroups"]:
        piston = bpy.data.objects.get(group["piston"])
        rod = bpy.data.objects.get(group["connectingRod"])
        throw = bpy.data.objects.get(throw_name_by_id.get(group["throw"], ""))
        pin = bpy.data.objects.get(group.get("wristPin") or "")
        if not (piston and rod and throw):
            missing.append({"group": group["id"], "piston": bool(piston),
                            "rod": bool(rod), "throw": bool(throw)})
            continue

        # Journal centre: prefer the refined (real cylindrical post)
        # centre from the radial-cluster pass over the classifier's
        # bbox-centre approximation.
        throw_part = next((p for p in C["parts"]
                           if p.get("throwId") == group["throw"]), None)
        if not throw_part:
            missing.append({"group": group["id"], "reason": "no_throw_part_record"})
            continue
        if group["throw"] in refined_journals:
            journal_centre_world = refined_journals[group["throw"]]
        else:
            journal_centre_world = _vec(throw_part["journalCentre"])

        # Look up piston centre from classification's parts list
        piston_part = next((p for p in C["parts"]
                            if p.get("name") == group["piston"]), None)
        piston_centre_world = _vec(piston_part.get("centre")) if piston_part else \
                              piston.matrix_world.translation.copy()

        # 2a) Journal target empty at journal centre, parented to throw.
        tgt_name = f"journal_target_{group['cylinderIndex']}_{group['bank']}"
        tgt = bpy.data.objects.get(tgt_name)
        if tgt is None:
            tgt = bpy.data.objects.new(tgt_name, None)
            coll.objects.link(tgt)
        tgt.empty_display_type = "SPHERE"
        tgt.empty_display_size = 0.4
        tgt.parent = None
        tgt.matrix_world = Matrix.Translation(journal_centre_world)
        wm = tgt.matrix_world.copy()
        tgt.parent = throw
        tgt.matrix_parent_inverse = throw.matrix_world.inverted()
        tgt.matrix_world = wm

        # 2b) Clear any previous SPATAIL constraints + animation on rod/piston
        for con in list(rod.constraints):
            if con.name.startswith("SPATAIL_"):
                rod.constraints.remove(con)
        rod.animation_data_clear()
        piston.animation_data_clear()

        # 2c) Unparent rod, MOVE rod so its origin (small-end) coincides
        # with the piston centre. Treatment already set rod.origin at the
        # PCA-detected small end, so this lands the wrist-pin joint exactly
        # at the piston centroid.
        if rod.parent is not None:
            wm = rod.matrix_world.copy()
            rod.parent = None; rod.matrix_world = wm
        rod.location = piston_centre_world

        # 2d) Detect rod's local long axis (post-origin-shift), set
        # Damped Track aiming the long axis at journal_target.
        track_axis = _detect_track_axis(rod)
        bpy.context.view_layer.update()

        # 2e) Parent rod under piston (so small-end translates with piston)
        wm = rod.matrix_world.copy()
        rod.parent = piston
        rod.matrix_parent_inverse = piston.matrix_world.inverted()
        rod.matrix_world = wm

        # 2f) Damped Track aimed at journal_target — pivots around the
        # rod's origin (= small-end). Big-end ends up orbiting the journal.
        dt = rod.constraints.new("DAMPED_TRACK")
        dt.name = "SPATAIL_rod_to_journal"
        dt.target = tgt
        dt.track_axis = track_axis

        # 2g) Wrist pin: move to piston centre and parent (visual detail)
        if pin:
            for con in list(pin.constraints):
                if con.name.startswith("SPATAIL_"):
                    pin.constraints.remove(con)
            pin.animation_data_clear()
            if pin.parent is not None:
                wm = pin.matrix_world.copy()
                pin.parent = None; pin.matrix_world = wm
            pin.location = piston_centre_world
            bpy.context.view_layer.update()
            wm = pin.matrix_world.copy()
            pin.parent = piston
            pin.matrix_parent_inverse = piston.matrix_world.inverted()
            pin.matrix_world = wm

        # 2h) Compute bore axis: piston-to-journal direction projected
        # perpendicular to the crank axis. This is what the animator uses
        # to drive the piston along its bore.
        rod_dir_world = piston_centre_world - journal_centre_world
        # Perpendicular to crank axis
        s = rod_dir_world.dot(crank_axis_dir_n)
        bore_perp = rod_dir_world - crank_axis_dir_n * s
        if bore_perp.length < 1e-4:
            bore_unit = Vector((0, 1, 0))
        else:
            bore_unit = bore_perp.normalized()

        # 2i) Stash slot data on the piston for the animator
        piston["spatail_slot"] = {
            "cyl": group["cylinderIndex"],
            "bank": group["bank"],
            "boreAxisUnit": list(bore_unit),
            "restPistonWorld": list(piston_centre_world),
            "journalWorld": list(journal_centre_world),
            "axisOriginWorld": list(crank_axis_origin),
            "axisDirectionWorld": list(crank_axis_dir_n),
            "conrodLength_cm": float(
                next((p["length_cm"] for p in C["parts"]
                      if p.get("name") == group["connectingRod"]), 17.0)
            ),
        }
        rigged += 1

    print(f"[rig_engine] {rigged} kinematic groups rigged, {len(missing)} missing")
    if missing: print(f"[rig_engine] missing details: {missing}")
    return {"rigged_groups": rigged, "missing": missing,
            "crank_axis_origin": list(crank_axis_origin),
            "crank_axis_direction": list(crank_axis_dir_n)}


print("[spatail_rig_engine] module loaded.")
