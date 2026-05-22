---
name: spatail-calibrate-assembly
description: Refine an already-rigged engine scene by measuring each rod's true small-end and big-end ring centres, shifting mesh data so the rod's local origin sits exactly at the small-ring centre, and sliding each piston along its bore axis so piston-to-journal distance equals the measured centre-to-centre rod length. After calibration the slider-crank formula holds with zero rod-length variance during animation.
when_to_use: Run AFTER spatail-rig-engine has produced a wired scene but BEFORE locking down the animation. Use when authored OBJ piston positions drift by ~1cm from where the real rod length wants them, or when verify-renders show the rod swinging through the wrong centre at the bottom of a stroke. Distinct from spatail-reassemble — reassemble builds an assembled scene from an empty start; calibrate refines pivots and spacing on an existing rig in place.
---

# Calibrate-Assembly Skill

## What this exists for

`spatail-rig-engine` assembles the kinematic chain by reading classification + treatment JSON, but it trusts treatment's PCA-derived rod pivot (small-end tip) and the OBJ's authored piston position. In practice the rod's true joint is the centre of the small-end ring, not the geometric tip — and the authored piston position is usually a centimetre or two off the journal-to-pivot distance the real rod demands. The result: animation runs but `|piston(t) − journal(t)|` is not constant, and verify-renders show the rod overshooting or missing the journal.

Calibrate-assembly fixes both errors at once, in the rigged scene, by directly measuring the mesh.

## What it solves

For each cylinder, calibrate:

1. **Find the rod's small-end ring centre** — slice 4–18% of the rod's vertices from the small end along its local long axis and take the world centroid.
2. **Find the rod's big-end ring centre** the same way.
3. **Compute the true centre-to-centre rod length** = ||big_ring_centre − small_ring_centre||.
4. **Shift mesh data along the long axis** so the rod's object origin coincides with the small-ring centre (geometry stays put in world).
5. **Rotate mesh data** so the rod's local +Y axis lines up with the small→big joint line. Guarded — skip rotation if it would flip more than 90° (indicates the detected "big end" is actually the small end). Once aligned, the Damped Track constraint uses `TRACK_Y`.
6. **Slide the piston along its bore axis** so piston-to-journal distance equals the measured c2c length. Lock the piston's Z so it stays in its cylinder plane.
7. **Re-parent the rod** to the piston at its new position with `matrix_parent_inverse` preserved so world position is stable.
8. **Update each piston's `spatail_slot`** — write the new `restPistonWorld` and `conrodLength_cm` so the animator's slider-crank uses the corrected rest pose.
9. **Re-run the animator** at the corrected rest pose so the saved .blend ships with valid keyframes.

## Inputs

- A rigged `.blend` file (produced by `spatail-rig-engine`) — every piston has a `spatail_slot` custom prop; every rod has a `SPATAIL_rod_to_journal` Damped Track constraint pointing at a `journal_target` empty.
- No JSON inputs. The skill reads geometry directly from the scene.

## Outputs

- A calibrated `.blend` file saved to `<out_blend_path>` with:
  - Mesh data shifted/rotated per rod so origins are at small-ring centres and local +Y points down the joint line.
  - Pistons translated along bore axes so piston-to-journal distance = true c2c length.
  - `spatail_slot.restPistonWorld` and `spatail_slot.conrodLength_cm` updated per piston.
  - Animator re-run, keyframes refreshed.
- A printed per-cylinder log: inset applied, c2c length, piston move distance.
- Returned list `per_cyl` with the same fields per cylinder (for caller inspection).

## How to invoke

```bash
blender --background <rigged.blend> --python C:/SPATAIL_MAX/pipeline/blender/spatail_calibrate_assembly.py -- <out.blend>
```

Or in Python:

```python
exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_calibrate_assembly.py").read())
calibrate(out_blend_path=r"C:/SPATAIL_MAX/assets_authoring/v10_engine_calibrated.blend")
```

## Anti-goals

- Do not rebuild the scene from scratch — that's `spatail-reassemble`. Calibrate assumes the rig already exists and only refines pivots / spacing.
- Do not re-classify parts. Roles, cyl, bank are already on `spatail_slot`; calibrate reads them, never overwrites them.
- Do not change rod geometry visually — `obj.data.transform(...)` shifts vertices so the geometry stays at the same world position; only the object origin moves.
- Do not touch parts without a `spatail_slot` and a `SPATAIL_rod_to_journal` child. Anything unrigged is left alone.

## Composition note

Pipeline position: `treat-mesh` → `classify-engine` → `reassemble` (or `rig-engine`) → **`calibrate-assembly`** → `animate-engine` → `segment-colors` / `multiview-render`.

Calibrate is the "final tighten" pass before animation locks in. After it runs, the slider-crank formula in `spatail-animate-engine` produces zero rod-length variance.
