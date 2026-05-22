---
name: spatail-rig-engine
description: Build the V/inline/boxer engine kinematic chain in Blender from treatment + classification JSON. Reparents throws under a crank empty, parents rods to pistons, drops journal_target empties at journal centres, adds Damped Track constraints. NO geometry inspection — only reads JSON. Outputs a scene where every piston has a `spatail_slot` custom prop the animator consumes.
when_to_use: After spatail-treat-mesh AND spatail-classify-engine have run. The scene should already contain the treated parts (origins normalized to pivots). This skill turns that pile of correctly-pivoted parts into a connected kinematic linkage.
---

# Engine Rigger

## Contract

**Input** (read-only):
- `<asset>.treatment.json` — knows pivot positions, shape classes.
- `<asset>.classification.json` — knows per-part roles + kinematic groups (one per cylinder).
- The Blender scene with treated parts present (origins at pivots).

**Output** (mutates scene):
- One `crank_assembly` empty at the crank axis, with all throws reparented under it.
- One `journal_target_<idx>_<bank>` empty per cylinder, parented to its throw at the journal centre.
- Each rod **moved** so its origin (already at small-end pivot, courtesy of treatment) coincides with its assigned piston's centroid → then **parented** to that piston.
- Each rod gets a Damped Track constraint aimed at its `journal_target` empty. The track axis is auto-detected per rod from its local bbox after origin shift.
- Each piston gets a `spatail_slot` custom property containing `boreAxisUnit / restPistonWorld / journalWorld / axisOriginWorld / conrodLength_cm` — everything the animator needs.
- Wrist pins moved to their assigned piston positions and parented (small visual detail).

## Why this skill is small

Every "interesting" decision — what's a piston, where's a pivot, which rod belongs to which cyl — was made upstream. This skill is purely **assembly + constraint hookup**:

```
for group in classification.kinematicGroups:
    snap_rod_to_piston(group.rod, group.piston)
    parent(group.rod, group.piston)
    journal_target = empty_at(throw.journalCentre, parented_to=throw)
    damped_track(group.rod, journal_target)
    stash_animator_props(group.piston, bore_axis, journal_rest, axis_origin)
```

## Anti-goals

- ❌ Inspect mesh geometry (compute PCA, find endpoints, check shape). Treatment did that.
- ❌ Decide cylinder pairings. Classification did that.
- ❌ Set object origins. Treatment did that. Touching origins here would re-introduce the pivot drift that broke earlier rigs.
- ❌ Drive motion. Animation is the next skill.

## How to invoke

```python
exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_rig_engine.py").read())
rig_engine(
    treatment_json=r".../v10_engine.treatment.json",
    classification_json=r".../v10_engine.classification.json",
)
```

Returns `{rigged_groups: int, missing_parts: [...]}`.
