---
name: spatail-measure-per-part
description: Per-part isolated measurement — moves each treated part to a "workshop" pose (no neighbours), runs shape-specific cylinder fits, paints vertex groups identifying each feature (journal-vertices, big-ring-vertices, etc.), writes one JSON per part with a schema appropriate to its shape class. Foundation of the deconstruct → measure → place pivot → rebuild architecture.
when_to_use: After spatail-treat-mesh + spatail-classify-* have produced treatment + classification JSONs. Run before spatail-reassemble. Variable per-part schema lets each part-type record only its kinematically relevant geometry.
---

# Per-Part Measurement Skill

## Why per-part isolation matters

Measuring features (journals, ring centres) while the engine is *assembled* keeps biting us:
- Vertex-mean axis origin drifts as the crank rotates (neighbour interference)
- RANSAC fits catch flange vertices from adjacent parts
- Bbox centroid of a throw shifts based on what mass dominates

**Fix**: take each part out of context. One part, one isolation pose, one measurement, one JSON. Reassemble later from those JSONs.

## What this skill produces

For each part identified by classification, a JSON file with **variable schema** based on shape class:

```
assets_processed/measurements/<asset_id>/
  ├─ <part_name>.measurements.json
  └─ _index.json   ← list of all parts, role, JSON path
```

**Schemas by shape class:**

| Shape | Schema |
|---|---|
| `rod-like` | `joint_rings: {small, big}`, `joint_axis_local`, `c2c_length_cm`, `pivot` |
| `blob` (crank throw) | `crank_axis_local`, `journal: {center_local, radius, throw_radius}`, `pivot` |
| `disc-like` (piston) | `bore_axis_local`, `wrist_pin: {center, axis}`, `pivot` |
| Anything else | `bbox`, `centroid`, `pivot: bbox_centre` |

## Vertex group painting

While measuring, the skill **paints vertex groups** identifying which vertices belong to each feature:

- `spatail.journal` — vertices on a throw's journal cylinder surface
- `spatail.counterweight` — vertices on the counterweight slab
- `spatail.big_ring` — vertices forming the conrod's big-end eye
- `spatail.small_ring` — vertices forming the conrod's small-end eye
- `spatail.rod_body` — everything else on a conrod

These vertex groups are the **ground truth** for downstream:
- `spatail-reassemble` reads them to set pivots
- The animator reads them to get the live journal position at any frame (without depending on a separately-placed empty)
- Visual segmentation can recolour them for the per-vertex segmentation render

## Workshop pose

When measuring, the skill briefly:
1. Stores each part's original parent + matrix_world
2. Unparents it
3. Translates it to an isolation zone (offset from the assembled engine)
4. Runs measurement
5. Restores parent + matrix_world

The mesh data and vertex groups stay in place; the temporary translation only affects ergonomics for visual inspection if needed.

## Invocation

```python
exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_measure_per_part.py").read())
measure_all_parts(
    treatment_json=r".../v10_engine.treatment.json",
    classification_json=r".../v10_engine.classification.json",
    out_dir=r"C:/SPATAIL_MAX/assets_processed/measurements/v10_engine",
)
```

Returns dict listing every part + its measurement JSON path.

## Anti-goals

- ❌ Move parts permanently — workshop pose is restored after measurement
- ❌ Decide kinematic relationships — that's reassemble's job
- ❌ Touch materials, lighting, cameras
