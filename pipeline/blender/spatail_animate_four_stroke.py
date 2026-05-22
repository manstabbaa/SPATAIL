"""
spatail_animate_four_stroke.py — v3 simple slider-crank projection.

PREREQ: spatail_rig_four_stroke.rig_four_stroke() has been run, so each
piston has a `spatail_slot` custom property with cyl/bank/boreAxisUnit/
restPistonWorld/journalWorld/axisOriginWorld/conrodLength_cm.

KINEMATICS:
  - crank_assembly rotates 0 → 4π over CYCLE frames (two crank revs).
  - Per piston: project the journal's orbit onto the bore axis.
      journal_now = R(theta) ⋅ (journal_rest − axis) + axis
      piston_displacement = (journal_now − journal_rest) · bore_unit
      piston.location = rest_piston + bore_unit * piston_displacement
    This is the simple projection v3 used. It's the "infinite-rod"
    approximation of slider-crank — accurate to a few mm for typical
    rod/stroke ratios (1.4+), much simpler than the full sqrt formula,
    and visually indistinguishable at this scale.
  - Rods need no keyframes — they're parented to pistons (small-end
    follows the wrist pin) with Damped Track aiming the rod's long axis
    at the journal_target empty (so big-end tracks the crank journal).
"""

import bpy, math
from mathutils import Vector


def animate_four_stroke(cycle_frames=120, rotations_per_cycle=2, sample_step=2):
    crank_assy = bpy.data.objects.get("crank_assembly")
    if crank_assy is None:
        print("[animate] no crank_assembly — run rig first"); return

    # ----- Crank rotation 0 → 4π --------------------------------------
    crank_assy.animation_data_clear()
    crank_assy.rotation_euler[2] = 0
    crank_assy.keyframe_insert("rotation_euler", index=2, frame=1)
    crank_assy.rotation_euler[2] = rotations_per_cycle * 2 * math.pi
    crank_assy.keyframe_insert("rotation_euler", index=2, frame=cycle_frames)
    # Best-effort linear interpolation on the crank (Blender 5.1 API)
    try:
        act = crank_assy.animation_data.action
        fcurves = getattr(act, "fcurves", None)
        if fcurves is None:
            for layer in getattr(act, "layers", []):
                for strip in getattr(layer, "strips", []):
                    for chan_bag in getattr(strip, "channelbags", []):
                        fcurves = getattr(chan_bag, "fcurves", None)
                        if fcurves:
                            for fc in fcurves:
                                for kp in fc.keyframe_points:
                                    kp.interpolation = "LINEAR"
        else:
            for fc in fcurves:
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"
    except Exception as e:
        print(f"[animate] linearisation skipped ({e})")

    # ----- Per-piston animation ---------------------------------------
    pistons = [o for o in bpy.data.objects
               if o.type == "MESH" and o.get("spatail_slot")]
    if not pistons:
        print("[animate] no rigged pistons found"); return

    animated = 0
    for piston in pistons:
        slot = piston["spatail_slot"]
        bore_unit = Vector(list(slot["boreAxisUnit"]))
        rest_piston = Vector(list(slot["restPistonWorld"]))
        journal_rest = Vector(list(slot["journalWorld"]))
        axis_origin = Vector(list(slot["axisOriginWorld"]))
        L = float(slot.get("conrodLength_cm", 15.5))  # rod c2c length

        # Crank rotates around +Z through axis_origin. Per-cyl geometry:
        #   r = throw radius (distance from crank axis to journal at rest)
        #   bore_unit = direction piston slides (perpendicular to crank Z)
        #   theta_rest = journal's angle at frame 1 (relative to bore_unit)
        # Slider-crank EXACT formula:
        #   piston_along_bore(θ) = r·cos(θ) + √(L² − r²·sin²(θ))
        # where θ is measured from the bore direction.
        # This guarantees |piston(t) − journal(t)| = L for every t.
        jrel_rest = Vector((journal_rest.x - axis_origin.x,
                             journal_rest.y - axis_origin.y, 0))
        r = jrel_rest.length
        if r < 1e-4:
            piston.animation_data_clear()
            piston.location = rest_piston
            piston.keyframe_insert("location", frame=1)
            piston.keyframe_insert("location", frame=cycle_frames)
            animated += 1
            continue

        # Find theta_rest: angle of journal at rest relative to bore direction
        perp_unit = Vector((0, 0, 1)).cross(bore_unit)  # right-hand perp to bore in XY
        cos_rest = jrel_rest.dot(bore_unit) / r
        sin_rest = jrel_rest.dot(perp_unit) / r
        theta_rest = math.atan2(sin_rest, cos_rest)

        # Exact piston-from-axis distance at theta=theta_rest
        disc_rest = L*L - (r * math.sin(theta_rest))**2
        p_rest = r * math.cos(theta_rest) + (math.sqrt(disc_rest) if disc_rest > 0 else 0)

        piston.animation_data_clear()
        for f in range(1, cycle_frames + 1, sample_step):
            d_theta = ((f - 1) / max(1, cycle_frames - 1)) * \
                      rotations_per_cycle * 2 * math.pi
            theta = theta_rest + d_theta
            # Exact slider-crank: piston distance from axis_origin along bore_unit
            disc = L*L - (r * math.sin(theta))**2
            p = r * math.cos(theta) + (math.sqrt(disc) if disc > 0 else 0)
            # Piston moves along bore_unit by (p − p_rest) from rest position.
            # This guarantees rod centre-to-centre = L throughout the cycle.
            piston.location = rest_piston + bore_unit * (p - p_rest)
            piston.keyframe_insert("location", frame=f)
        piston.location = rest_piston
        piston.keyframe_insert("location", frame=cycle_frames)
        animated += 1

    print(f"[animate] crank cycle={cycle_frames}f  pistons animated={animated}")
    return {"cycleFrames": cycle_frames, "pistonsAnimated": animated}


print("[spatail_animate_four_stroke] module loaded.")
