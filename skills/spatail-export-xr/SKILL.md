---
name: spatail-export-xr
description: Export the current Blender scene as a `.spatail` bundle the iOS AR player consumes. Packages USDZ geometry + the v0.5 SpatialExperienceContract + a prims-index + hero preview frames into a single zipped artefact tagged `com.spatail.experience`. The iOS app binary never changes — new explanations are new bundles.
when_to_use: As the final stage of the SPATAIL pipeline, after treat-mesh → merge-intelligence → classify-* → reassemble → animate (any subset). The bundle is the unit of distribution between Blender and the iOS player.
---

# Export-XR Skill

## What it solves

The pipeline produces a fully-authored Blender scene (geometry, animation, classification, mechanics). The iOS app is a **generic player** — it must consume that scene as data, with no code changes per prompt. This skill is the bridge.

One bundle = one explanation. Drop it into the iOS app via AirDrop / URL / share sheet, and it plays.

## Algorithm

1. **Collect logical meshes** — every mesh that isn't a SPATAIL helper or the camera.
2. **Build prims index** — slug each Blender object name to a valid USD prim identifier (e.g. `Steering Wheel.123` → `Steering_Wheel_123`), guarantee uniqueness, rename Blender objects to match so the USD export uses the slugged names verbatim.
3. **Export USDZ** via `bpy.ops.wm.usd_export` under a window context override, with:
   - `convert_orientation=True`, `export_global_up_selection="Y"`, `export_global_forward_selection="NEGATIVE_Z"` — USDZ convention.
   - `meters_per_unit=0.01` if scene is cm-authored, else `1.0` — bakes the unit conversion at export.
   - `generate_preview_surface=True` — USD Preview Surface materials RealityKit consumes directly.
   - `root_prim_path="/Scene"` — matches the prims-index paths.
4. **Render hero frames** — OpenGL viewport renders from front + perspective + a 512² thumbnail. These give the iOS picker a cover image and provide a fallback panel if USDZ fails to load.
5. **Write `experience.json`** — either copy the orchestrator output (when called from the planner) or emit a placeholder v0.5 contract with each part as a `three_d_model` element.
6. **Write `manifest.json`** — top-level pointers, schema version, bbox in meters, supported scale modes.
7. **Write `prims_index.json`** — `primToElement` + `elementToPrim` lookups for the iOS app's tap-to-element resolution.
8. **Zip** the stage directory as `<asset>.spatail`.

## Inputs

- A Blender scene with logical meshes already merged (post merge-intelligence). Working from 5000 sub-meshes will produce a slow-loading USDZ on iOS.
- Optional: `experience_json_path` — a path to an orchestrator-generated `.experience.json`. If omitted, a minimal placeholder is emitted.
- Optional: `anim_frame_range=(start, end)` — bake animation tracks for the playable range.

## Outputs

A single `<bundle>.spatail` file:

```
<bundle>.spatail/
  manifest.json
  experience.json
  scene.usdz
  prims_index.json
  hero/
    front.jpg
    perspective.jpg
    thumbnail.jpg
  source/
    prompt.txt
```

Typical size for a 79-part wheel: ~3 MB. The wheel scene's USDZ alone is ~8 MB; ZIP compression brings it down.

## How it composes

```
treat-mesh
 ↓
merge-intelligence       ← keep mesh count manageable (target <500)
 ↓
classify-* / reassemble  ← bake transforms, set pivots
 ↓
animate                  ← keyframes
 ↓
export-xr                ← THIS — produces .spatail
 ↓
iOS player               ← outside this repo; see docs/xr/IOS_APP_ARCHITECTURE.md
```

## Anti-goals

- ❌ Re-author the scene at export. Bake everything upstream — drivers, constraints, modifiers.
- ❌ Generate explanation text. That's the orchestrator's job; export-xr only packages.
- ❌ Re-run merge/cluster. Mesh count is whatever the scene has when you call the function.
- ❌ Ship .blend or raw FBX. The contract is `.spatail` only; no other format is consumed by the iOS player.

## Related docs

- `docs/xr/IOS_BUNDLE_SPEC.md` — the bundle contract the iOS app must implement.
- `docs/xr/IOS_APP_ARCHITECTURE.md` — suggested RealityKit / Swift architecture for the iOS player.
- `pipeline/spatail/experience_contract.js` — the v0.5 contract this skill bakes into `experience.json`.
