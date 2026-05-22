# SPATAIL Blender Toolset — Master Reference

The SPATAIL toolset turns raw CAD files into rigged, animated, visually explained spatial content. Every skill in here is **invokable on its own**; the skills compose into the full pipeline.

## Architectural principle

Treat 3D the way physical engineering does it:

> **Deconstruct → Measure → Place pivots → Rebuild from scratch.**

Don't fix mesh in context — too many features bias each other. Take it apart, measure each piece in isolation, set pivots on the workbench, assemble from a blueprint.

## Pipeline overview

```
RAW CAD                                                              FINAL OUTPUT
  │                                                                          ▲
  ▼                                                                          │
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│  treat-mesh      │ →  │  classify-*      │ →  │  measure-per-    │ →  │  reassemble     │
│  (asset-agnostic)│    │  (per asset-type)│    │  part            │    │  (rebuild       │
│                  │    │                  │    │  (isolated meas) │    │   from blueprint)│
└──────────────────┘    └──────────────────┘    └──────────────────┘    └────────┬────────┘
                                                                                  │
                                                                                  ▼
                            ┌───────────────────┐    ┌───────────────────┐    ┌──────────────────┐
                            │  segment-colors   │ ←  │  scale-normalize  │ →  │  animate-*       │
                            │  (verify-vis)     │    │  (real-world)     │    │  (per mechanism) │
                            └───────────────────┘    └───────────────────┘    └──────────────────┘
                                                                                       │
                                                                                       ▼
                                                                              ┌──────────────────┐
                                                                              │ realistic-       │
                                                                              │ materials +      │
                                                                              │ render-to-mp4    │
                                                                              └──────────────────┘
```

## Skills (invokable)

| Skill | Stage | What it does | Output |
|---|---|---|---|
| `spatail-treat-mesh` | Foundation | Imports, segments by loose-parts, PCA, basic pivot heuristic, normalizes origins | `<asset>.treatment.json` |
| `spatail-classify-engine` | Semantic | Engine-specific: identifies throws/pistons/rods/pins, builds kinematic groups | `<asset>.classification.json` |
| `spatail-measure-per-part` ★ | Measurement | Moves each part to isolation, runs shape-specific cylinder fits, writes **per-part JSON with variable schema** | `<asset>/measurements/<part>.json` |
| `spatail-rig-engine` | Rig | Builds crank assembly, journal targets, rod-piston parenting, Damped Track | rigged scene |
| `spatail-reassemble` ★ | Rebuild | Reads per-part measurements + kinematic blueprint, builds engine from scratch | clean rigged scene |
| `spatail-scale-normalize` ★ | Scale | Detects/applies real-world scale (1 BU = 1 cm), captures ruler grid | scaled scene + ruler.png |
| `spatail-segment-colors` | Verify | Per-part viewport + render colours for visual debugging | `<asset>_segmented.blend` + legend |
| `spatail-realistic-materials` ★ | Export | Replaces segmentation with PBR materials | final-export-ready scene |

★ = new in this build cycle.

## Modules (helpers, not standalone)

| Module | Purpose |
|---|---|
| `spatail_cylinder_fit` | RANSAC + Kasa 2D circle fitting (used by treat, measure, reassemble) |
| `spatail_calibrate_assembly` | Post-rig pivot refinement (will be folded into reassemble) |
| `spatail_animate_four_stroke` | Slider-crank piston driver — **upgrading to exact formula** |
| `spatail_multiview_render` | 4-camera grid (persp/front/right/top) for visual verification |
| `spatail_calibration_grid` | Glowing pivot-axis overlay for visible quality |
| `spatail_verify_rig` | Headless rig diagnostics → JSON |

## Data formats

Each stage writes its own JSON. Downstream stages read upstream JSON, never raw mesh.

### `<asset>.treatment.json`
Per-part topology + PCA + basic pivot. Asset-agnostic.

### `<asset>.classification.json`
Per-part role assignment + kinematic groups. Asset-type-specific (engine for now).

### `<asset>/measurements/<part>.json` ★
**Variable schema per shape class.** Each part stores only what's relevant to its kinematic role:

```json
// rod-like
{
  "shape_class": "rod-like",
  "joint_rings": {
    "small": {"center_local": [0,0,0], "radius_cm": 0.6, "fit_residual_mm": 0.05},
    "big":   {"center_local": [0, 15.8, 0], "radius_cm": 0.9, "fit_residual_mm": 0.05}
  },
  "joint_axis_local": [0, 1, 0],
  "c2c_length_cm": 15.85,
  "pivot": "joint_rings.small.center_local"
}

// blob (crank throw)
{
  "shape_class": "blob",
  "crank_axis_local": [0, 0, 1],
  "journal": {"center_local": [-4.84, 4.34, 0], "radius_cm": 0.748,
              "throw_radius_cm": 6.46, "fit_residual_mm": 0.05},
  "pivot": "journal.center_local"
}

// disc-like (piston)
{
  "shape_class": "disc-like",
  "bore_axis_local": [0, 1, 0],
  "wrist_pin": {"center_local": [0,0,0], "axis_local": [1,0,0]},
  "pivot": "wrist_pin.center_local"
}
```

Downstream code asserts the part-type and pulls the fields it expects.

## Why the new skills earn their slot

- **`measure-per-part`** decouples measurement from rig state. We've been bitten repeatedly by measurements drifting as the rig changed (vertex-mean axis-origin moving with rotation, RANSAC catching flange vertices on rotated meshes). Per-part isolation eliminates context contamination.

- **`reassemble`** makes the rig idempotent and deterministic. Re-run it on the same measurements, you get the same scene. Bugs in older rigging passes don't propagate.

- **`scale-normalize`** lets us reason about real units. The OBJ ships in unknown units; the engine bbox came out 35×30×62 cm. If the user says "this is actually a 4L V10 → 24 cm bore spacing", we conform geometry to that scale and downstream physics (e.g. piston speed) becomes meaningful.

## Stage I/O contract

Every skill's contract:
- Input: prior-stage JSON(s) + optional scene state
- Output: its own JSON file + (optionally) scene mutation
- No skill reads raw mesh except `treat-mesh` and `measure-per-part`
- All other skills work from JSON. This makes them headlessly testable and reorderable.

## Where the V10 currently stands

| Stage | Status |
|---|---|
| treat-mesh | ✓ 34 parts, basic pivots set |
| classify-engine | ✓ 10 kinematic groups, 0 missing |
| measure-per-part | ⏳ being built this turn |
| rig-engine | ✓ rigged + calibrated |
| reassemble | ⏳ being built this turn |
| scale-normalize | ⏳ being built |
| segment-colors | ✓ working, used for debugging |
| animate | partial — slider-crank approx, needs exact formula |
| realistic-materials | ⏳ deferred |

## Next-up

1. Build `measure-per-part` → produce 34 per-part JSONs for V10
2. Build `reassemble` → rebuild V10 from scratch using those JSONs
3. Fix `animate` slider-crank formula
4. Build `scale-normalize` + ruler grid
5. Run full pipeline end-to-end against a non-engine asset (steering wheel) — final asset-agnosticism test
6. `realistic-materials` for export-quality output
