---
name: spatail-motion-validator
description: Renders a "mugshot grid" of an asset across animation phases × camera angles with a coordinate ruler overlay, measures per-part world displacement at each phase, groups parts into motion cohorts, and cross-references actual cohorts against the asset's declared kinematic groups. Catches the class of bug where some parts of a "rotor" or "linkage" stay stationary while their siblings move (mesh-parenting bugs, orphaned sub-meshes from CAD import). Outputs a structured validation report and an Opus-vision review of the grid.
when_to_use: After any animation bake or asset export. Also run before shipping a new asset to the web runtime so we know visually + numerically that the animations actually move what they should. Use as the LAST gate before publishing — the same way you'd inspect a part on a calibration jig before shipping.
---

# Motion Validator

## Why this exists

A pure-numbers test ("did mesh48 move?") catches some bugs but not all.
A pure-vision test ("does the fan look right?") catches different bugs.
We need both, plus the spatial context to tell them apart. The model for
this is forensic photography: a subject is photographed from multiple
fixed angles (mugshot views), each shot has a scale ruler in frame, and
the resulting grid is the evidence.

The fan-blade-separation bug (some blades spin, others stay still) was
INVISIBLE to the existing visual_validator because it only saw single
canvas captures from one angle. The same scene from the side or back
would have made the bug obvious. Multi-angle + multi-phase is the fix.

## What it does

For each animation clip in `<asset>_animation_library.json`:

1. **Identifies the expected kinematic group** — reads
   `part_registry.json.kinematicGroups` (e.g. `rotor` for the fan).
2. **Samples 4 phases** of the clip: 0% (rest), 25%, 50%, 75%.
3. **At each phase**, snapshots `world_position` of every mesh in the
   scene + every part referenced in the kinematic group.
4. **At each phase**, renders the asset from N **mugshot views**
   (front, back, left, right, top, 3q) with a coordinate grid overlay
   and a 1cm scale ruler in the bottom-left.
5. **Computes motion cohorts** — clusters parts whose 4-phase
   displacement vectors are nearly equal (within 0.5mm). Parts in the
   same cohort moved together.
6. **Cross-references** against the kinematic group:
   - `stationary_but_should_have_moved` — parts in the group whose
     cohort is the "stationary" one
   - `moved_but_not_in_group` — parts that moved with the cohort but
     are NOT listed in the group (orphaned blade sub-meshes!)
   - `motion_consistency_score` — what fraction of intended members
     moved together as expected
7. **Opus vision pass** over the mugshot grid — catches non-numeric
   problems: torn meshes, wrong-color materials, scale glitches,
   parts intersecting other parts.
8. **Writes** `<out>/<asset>/<clip>/_validation.json` with the structured
   report + paths to all renders.

## Mugshot views

| View         | Camera position relative to asset bbox             | Used for |
|--------------|----------------------------------------------------|----------|
| `front`      | `[0, 0, -1.6 × bbox_diag]`                         | Symmetry, blade-tip alignment |
| `back`       | `[0, 0, +1.6 × bbox_diag]`                         | Frame backside, motor exposure |
| `left`       | `[-1.6 × bbox_diag, 0, 0]`                         | Profile silhouette, depth check |
| `right`      | `[+1.6 × bbox_diag, 0, 0]`                         | Profile mirror, asymmetry |
| `top`        | `[0, +1.6 × bbox_diag, 0]`                         | Plan view, rotor centring |
| `three_qtr`  | `[+0.8, +0.6, -0.8] × bbox_diag`                   | The "hero" view a human would see |

The 1.6× bbox_diag distance keeps each view at consistent zoom across
asset scales. The grid overlay is a fixed 1cm spacing in world units —
so a 60mm fan and a 750mm engine both get a usable scale.

## Output

```
out/<asset>/<clip>/
  phase_00/{front,back,left,right,top,three_qtr}.png
  phase_25/{front,back,left,right,top,three_qtr}.png
  phase_50/{front,back,left,right,top,three_qtr}.png
  phase_75/{front,back,left,right,top,three_qtr}.png
  _measurements.json   — per-part per-phase world positions
  _cohorts.json        — motion clusters
  _validation.json     — final findings + Opus review
  grid.png             — assembled 4×6 contact sheet for the vision pass
```

## How to invoke

```python
exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_motion_validator.py").read())
validate_asset(
  asset_id="fan",
  asset_glb=r"C:/SPATAIL_MAX/engineexplainer/engine/fan.glb",
  registry_path=r"C:/SPATAIL_MAX/engineexplainer/engine/fan_part_registry.json",
  anim_library_path=r"C:/SPATAIL_MAX/engineexplainer/engine/fan_animation_library.json",
  out_root=r"C:/tmp/motion_validate",
  vision_pass=True,
)
```

Returns: the validation summary (overall verdict + counts of each
finding type). The full report + renders are on disk.

## Anti-goals

- ❌ Bake animations. That's the animator's job.
- ❌ Fix the bugs it finds. It reports; humans or the orchestrator decide.
- ❌ Replace the schema critic or the semantic validator. This is the
  **last** gate, focused on motion + geometry + visual integrity, after
  schema + content are already OK.
- ❌ Long-running renders. Uses the workbench engine at low res (640×480)
  for speed — the goal is diagnostic clarity, not beauty.

## Composition

Pipeline position:

```
mechanic → director → critic → semantic-validator → contract → runtime
                                                              ↓
                                                       motion-validator
                                                              ↓
                                                          report / gate
```

Triggered by the orchestrator after any successful animation bake or
asset re-export — before the GLB is published to the web runtime.
