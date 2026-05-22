---
name: spatail-segment-colors
description: Assigns deterministic distinct viewport + render colors to every part in a Blender scene so screenshots/renders are unambiguously segmented. Each part gets a unique colour, parts of the same role family share a hue band so they read as a group. Used during iteration so the agent can SEE which rod/piston/throw is which in MCP screenshots and reason about placement issues. Saves to a separate `<asset>_segmented.blend` so the original stays clean for realistic-material export.
when_to_use: When iterating on rig/animation and visual inspection is needed — especially when many parts look similar (e.g. 10 identical rod meshes) and the agent needs to identify which is which from a render. Run AFTER spatail-rig-engine (so each part has a known role + cyl/bank). NOT for final exports — use spatail-realistic-materials for that.
---

# Segment-Colors Skill

## What it does

Walks every mesh in the scene, looks up its role and instance ID from rig data (`spatail_slot` custom properties, parent relationships, etc.), and assigns:

- A **material** with a Principled BSDF whose base colour is deterministic.
- The same colour as **viewport display colour** (so Solid view also shows the segmentation).
- The **object colour** (so Object-mode display colour can be used if needed).

The colour map:

| Role | Hue band | Variations |
|---|---|---|
| `crank_throw` | red (0.00) | by cyl index → 5 distinct reds |
| `connecting_rod` | green (0.33) | by (cyl, bank) → 10 distinct greens |
| `piston` | blue (0.60) | by (cyl, bank) → 10 distinct blues |
| `wrist_pin` | orange (0.10) | by (cyl, bank) → up to 10 distinct oranges |
| Unclassified | grey | mid-grey, signals "I couldn't identify this" |

Within each hue band, value and saturation are varied so individual instances are distinguishable. Bank-A vs Bank-B varies the value (B is brighter), and cylinder index varies the hue by a few percent.

## Contract

**Input** (scene state):
- A Blender scene that has been rigged by `spatail-rig-engine`, so pistons have `spatail_slot`, rods have `SPATAIL_rod_to_journal` constraints, throws are parented to `crank_assembly`, and wrist pins are children of pistons.

**Output**:
- Materials created/assigned on every relevant mesh.
- A separate `.blend` file saved alongside the working scene: `<source>_segmented.blend`.
- A small JSON `<source>_segmentation.json` listing every part → assigned colour (for later cross-reference).

## How to invoke

```python
exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_segment_colors.py").read())
apply_segmentation_colors(
    save_as=r"C:/SPATAIL_MAX/assets_authoring/v10_engine_segmented.blend",
    write_legend=r"C:/SPATAIL_MAX/assets_processed/treated/v10_engine/v10_engine.segmentation.json",
)
```

Re-runs are idempotent — re-applying the colours to the same scene gives the same result.

## Anti-goals

- ❌ Replace existing PBR materials permanently. The skill writes to a *separate* .blend so the original isn't touched.
- ❌ Identify parts. Classification is done upstream; this skill only colours what's already labelled.
- ❌ Final-export visuals. Run `spatail-realistic-materials` instead before export.

## Composition note

Pipeline position: `treat-mesh` → `classify-engine` → `rig-engine` → `calibrate-assembly` → `animate-engine` → **`segment-colors`** → `multiview-render` (verification) → `realistic-materials` (final export).

Segment-colors is a verification-iteration skill: writes to a *separate* `_segmented.blend` so the calibrated/animated source stays pristine. The final export path swaps these viewport-segmentation materials for PBR materials before render.
