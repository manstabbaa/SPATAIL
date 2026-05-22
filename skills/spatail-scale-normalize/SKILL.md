---
name: spatail-scale-normalize
description: Normalize the absolute scale of an imported CAD so that downstream tolerances (eps, merge-distance, pivot-search radius) operate in real-world centimetres. OBJ/FBX/STL files arrive in arbitrary units — sometimes mm, sometimes cm, sometimes m, sometimes m-with-unit_scale=0.01. Detect the actual real-world size via bbox diagonal + an asset class hint (or LLM thumbnail judgement), then scale the scene so one Blender unit equals one centimetre. Writes a `scale_normalization.json` audit. Asset-agnostic.
when_to_use: Run this BEFORE spatail-treat-mesh on any newly imported asset whose units are unknown or untrusted. The F1 wheel that imported at 180 Blender units is the canonical case — it could be 180 cm (correct, model-in-cm), 180 mm (way too small), or 1.8 m (model-in-m with unit_scale=0.01). All downstream skills assume cm; if scale is wrong by 10×, every eps/threshold breaks silently.
---

# Scale Normalize Skill

## What this exists for

Every downstream skill in the SPATAIL pipeline assumes **one Blender unit == one centimetre**. The pivot-search radius in `spatail-treat-mesh`, the DBSCAN epsilon in `spatail-cluster-parts`, the merge-touching distance, the part-classification size buckets — all of these are hard-coded in cm. If a CAD ships at the wrong scale, none of these thresholds fire correctly and the bug surfaces three skills downstream as "the clusterer made one giant cluster" or "every part is rod-like."

CAD files lie about units. OBJ has no unit metadata. FBX has a `UnitScaleFactor` that exporters routinely set wrong. STL is literally just numbers. We cannot trust the file.

What we CAN do reliably: read the bbox diagonal, know roughly how big the asset *should* be in the real world (an F1 steering wheel is ~32 cm across; a V10 engine is ~80 cm long; a car is ~450 cm), and compute the corrective factor.

## Detection

1. After import, compute the asset bbox diagonal in current Blender units.
2. Obtain a target real-world diagonal in centimetres. Sources, in order of preference:
   - **explicit** — caller passes `target_diagonal_cm=32.0` because they measured the real part.
   - **class hint** — caller passes `asset_hint="steering_wheel"` and the skill looks up a coarse expected size from a built-in table.
   - **LLM judgement** — caller renders a thumbnail and asks an LLM "roughly how big is this object in cm?" (out of scope for the module itself; the caller supplies the number).
3. Compute `factor = target_diagonal_cm / current_diagonal`.
4. If `0.95 <= factor <= 1.05`, no-op (already in cm).
5. If `factor` is wildly off (>1000× or <0.001×), refuse and emit a warning — likely the asset hint is wrong or the file is empty.

## Normalization

- Select all mesh objects.
- Apply `obj.scale = (factor, factor, factor)` then bake with `transform_apply(scale=True)`.
- This shifts vertex coordinates so geometry now lives at real-world cm.
- Object origins move proportionally; that's fine — `spatail-treat-mesh` will re-normalize them anyway.

## Persistence

Write `<out_dir>/scale_normalization.json`:

```json
{
  "assetId": "f1_steering_wheel",
  "schemaVersion": "0.1.0-spatail-scale-normalize",
  "normalizedAt": "2026-05-22T...",
  "before": {
    "bbox_diagonal_blender_units": 180.2,
    "bbox_lo": [...], "bbox_hi": [...]
  },
  "target": {
    "source": "class_hint",
    "asset_hint": "steering_wheel",
    "target_diagonal_cm": 32.0
  },
  "factor_applied": 0.1776,
  "after": {
    "bbox_diagonal_cm": 32.0,
    "bbox_lo": [...], "bbox_hi": [...]
  },
  "skipped": false,
  "warnings": []
}
```

The factor is what auditors care about most: a value of ~0.1 means the file was in mm, ~1.0 means cm (no-op), ~100 means m. Anomalous factors are a signal that something is wrong upstream.

## Class hint table (built-in fallback)

When `target_diagonal_cm` is not supplied, the module looks up `asset_hint` in:

| hint                 | expected diagonal (cm) |
|----------------------|------------------------|
| `steering_wheel`     | 32                     |
| `engine`             | 80                     |
| `dashboard`          | 180                    |
| `hand_tool`          | 25                     |
| `full_car`           | 450                    |
| `chair`              | 90                     |
| `desk`               | 160                    |

This table is deliberately small. Add entries only when a new asset class is being onboarded — keeping it small forces callers to think about each class and supply an explicit measurement when possible.

## When to use

Run scale-normalize **before** `spatail-treat-mesh` on any asset whose units are unknown or untrusted. The full sequence on a new CAD:

```
import OBJ
  ↓
spatail-scale-normalize    ← this skill (bring to real cm)
  ↓
spatail-treat-mesh         (segment, PCA, pivots)
  ↓
spatail-cluster-parts      (only if part-count > 100)
  ↓
spatail-classify-* / measure / rig / animate
```

If you already trust the scale (e.g. an asset you authored in Blender at 1 unit = 1 cm), you can skip this. But if `treat-mesh` results look weird — every part is "rod-like", the DBSCAN epsilon collapses everything into one cluster, or the unit_guess in the manifest says "mm" — come back here first.

## How to invoke

```python
exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_scale_normalize.py").read())

# Option A: explicit target (best when you have a measurement)
result = normalize_scale_in_scene(target_diagonal_cm=32.0,
                                  out_dir=r"C:/SPATAIL_MAX/assets_processed/treated/f1_wheel",
                                  asset_id="f1_wheel")

# Option B: class hint (uses the built-in table)
result = normalize_scale_in_scene(asset_hint="steering_wheel",
                                  out_dir=r"C:/SPATAIL_MAX/assets_processed/treated/f1_wheel",
                                  asset_id="f1_wheel")
```

Returns a dict with `factor_applied`, `before`, `after`, and `skipped`. Also writes `<out_dir>/scale_normalization.json`.

## Anti-goals (what this skill does NOT do)

- ❌ **Do not detect scale from features.** No RANSAC for "standard bolt thread diameter", no CNN that recognises "this is a chair so it must be 90 cm." Feature-based scale detection is fragile and asset-class-coupled — exactly what we're trying to avoid. Bbox + class hint is crude but auditable and impossible to surprise you.
- ❌ Do not rotate or re-orient the asset. Axis conventions (Y-up vs Z-up) are baked at import time by `treat-mesh`'s `transform_apply`; we only touch scale.
- ❌ Do not change object origins. That's `treat-mesh`'s job.
- ❌ Do not touch materials, shading, or animation.
- ❌ Do not try to detect scale across multiple objects independently. The asset is one rigid body for scale purposes; one factor applies to the whole scene.
- ❌ Do not silently apply a huge factor (>1000× or <0.001×). Refuse and warn — the hint is almost certainly wrong.

These boundaries keep the skill a thin, idempotent gate at the top of the pipeline. If you find yourself reaching for cleverness here, you are about to make the pipeline less debuggable.
