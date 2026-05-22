---
name: spatail-animate-engine
description: Drive slider-crank piston motion and journal-tracking rod animation on a rigged + calibrated engine scene. Rotates `crank_assembly` 0→4π over the cycle (two crank revolutions = one four-stroke cycle), then per piston keyframes its location along the bore axis using the exact slider-crank formula `r·cos(θ) + √(L² − r²·sin²(θ))` so |piston − journal| stays equal to the rod's centre-to-centre length throughout. Rods need no keyframes — they're parented to pistons with Damped Track aimed at the journal target empty.
when_to_use: Run AFTER spatail-rig-engine and (ideally) spatail-calibrate-assembly have produced a scene where every piston has a `spatail_slot` custom prop with `boreAxisUnit`, `restPistonWorld`, `journalWorld`, `axisOriginWorld`, and `conrodLength_cm`. Use when you need the scene to play back the four-stroke cycle at 24fps for renders, exports, or visual verification of the rig.
---

# Animate-Engine Skill

## What this exists for

Once the rig is built (parts parented, constraints wired, slots populated) and calibrated (rod centres measured, pistons spaced correctly), the only thing left is to drive the linkage. This skill knows nothing about geometry — it reads the per-piston `spatail_slot` and stamps keyframes.

The kinematics are:

- **Crank**: `crank_assembly.rotation_euler[2]` keyframed linearly from 0 at frame 1 to `rotations_per_cycle · 2π` at frame `cycle_frames`. Default 2 revolutions over 120 frames = one four-stroke cycle.
- **Per piston**: project the journal's circular orbit onto the bore axis and use the exact slider-crank formula. With `r` = throw radius, `L` = rod c2c length, `θ` = crank angle from bore direction:

      piston_along_bore(θ) = r·cos(θ) + √(L² − r²·sin²(θ))

  This guarantees `|piston(t) − journal(t)| = L` for every frame. Piston location is `restPistonWorld + boreAxisUnit · (p − p_rest)`.
- **Rods**: no keyframes. They follow the piston via parenting (small-end stays at the wrist pin) and aim their long axis at the `journal_target` empty via the Damped Track constraint installed by the rigger. Big-end therefore tracks the crank journal automatically.

`sample_step=2` cuts keyframes every other frame for file-size economy; Blender interpolates the rest.

## Inputs

- A rigged scene (`crank_assembly` empty exists at the crank axis; pistons carry `spatail_slot`). Ideally calibrated.
- `classification.json` is consumed earlier by the rig step; this skill reads only the scene's custom props at runtime.
- Animation parameters:
  - `cycle_frames` (default 120) — frames per complete four-stroke cycle.
  - `rotations_per_cycle` (default 2) — crank revolutions per cycle.
  - `sample_step` (default 2) — keyframe stride per piston.

## Outputs

- Keyframes on `crank_assembly.rotation_euler[2]` (linear interpolation).
- Keyframes on every piston's `location` channel along its bore axis.
- Returns `{cycleFrames, pistonsAnimated}`. Rods and journal targets need no keyframes — constraints + parenting handle them.

## How to invoke

```python
exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_animate_four_stroke.py").read())
animate_four_stroke(cycle_frames=120, rotations_per_cycle=2, sample_step=2)
```

`spatail-calibrate-assembly` calls this automatically at the end of its run so the saved calibrated `.blend` ships with valid keyframes.

## Anti-goals

- Do not infer geometry. Every value (`boreAxisUnit`, `restPistonWorld`, `journalWorld`, `axisOriginWorld`, `conrodLength_cm`) is read from `spatail_slot`. If a slot is missing, the piston is skipped.
- Do not keyframe rods. Their motion is fully derived from parent + Damped Track. Adding keys would fight the constraint.
- Do not change rig topology (parent relationships, constraint targets). That's the rigger's job.
- Do not bake non-engine motion (camera, lights, environment). Animate-engine drives the linkage only.

## Composition note

Pipeline position: `treat-mesh` → `classify-engine` → `reassemble` / `rig-engine` → `calibrate-assembly` → **`animate-engine`** → `segment-colors` / `multiview-render` → final render or USDZ export.

Animate is the last step that mutates per-frame state for the engine itself. Anything visual that comes after (colours, multi-view renders, materials) reads the animated scene without changing the linkage.
