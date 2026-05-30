---
name: spatail-model-from-manual
description: Generative asset path for usermanualXR — when a manual has no matching library model, BUILD one. Takes a per-part build plan (primitives + transforms + roles), constructs the asset in a dedicated Blender scene, emits a walkthrough-compatible part registry, exports a meters-scale GLB (with sub-mesh region overlays), registers it as a library asset, and lets the normal manual→contract staging produce a playable step-by-step demo. Use when match_product returns None, or to author a new flat-pack/assembly asset from primitives.
when_to_use: When asset_library.match_product() misses (the "No library asset matched 'X'" case), or whenever an asset is simple enough to build from boxes/cylinders/tubes (furniture, enclosures, brackets, frames). For organic or high-detail surfaces, build the primitive base here, then refine with spatail-mesh-select before export.
---

# Generative Modeling From a Manual

The user's workflow: **understand the manual → per-part plan → build parts from
primitives → combine → multiview refine → step-by-step interactive demo.** This
skill is the build-and-ship half; spatail-mesh-select is the refine half.

The architectural point: a manual that matches no curated model is not a dead
end. It is the trigger to *generate* the asset, register it, and run the same
staging the curated assets use. The generated asset is a first-class library
asset afterward.

## Automated path (one call — segment → build → register → stage)

This is now wired end-to-end. A dropped manual runs:

```
manual text → manual_segment.segment_manual()      # parts + steps + assembles
            → generative_bridge.build_and_register() # headless Blender build + register
            → walkthrough.build_walkthrough(mode="generate")  # explode/assemble beats
            → contract (scene.assembly + meta.camera_presets) → web mini-app
```

```python
from engineexplainer.intelligence import walkthrough
result = walkthrough.build_walkthrough(manual_text, mode="generate")
# result["asset_id"] == "gen_<slug>"; result["contract"] is playable
```

Or over HTTP (the mini-app's call): `POST /api/manual {manual_text, mode:"generate"}`.
The **Manual → XR mini-app** (`web/manual.html` + `web/src/manual_app.js`) is the
home for this path — it loads the GENERATED GLB (`contract.meta.asset`) with
asset-scaled cameras (`contract.meta.camera_presets`) and plays the runtime-tween
assembly (`contract.scene.assembly`), never a curated model.

Key facts that make the automated path robust:

- **The segment agent emits `assembles` per step** — the parts each step seats.
  The walkthrough explodes the unit on the identify beat and assembles exactly
  those parts per step, so the product builds itself part-by-part on screen.
  (See the `manual-segmenter` subagent: `.claude/agents/manual-segmenter.md`.)
- **Assembly is a RUNTIME tween over per-part offsets**, not baked glTF clips.
  The driver writes `assembly.offsets` (role-aware, in the glTF Y-up frame) into
  the registry; the viewer captures each part's seated rest **once at load** and
  explode/assemble translate between rest and rest+offset.
- **Generated assets register + persist automatically** (`asset_library.register_generated_asset`
  → `engine/generated_assets.json`), so they survive a 5175 restart with no hand-wiring.

## Stage map (files involved)

1. **Segment** — `intelligence/manual_segment.py` → `{parts[], steps[](+assembles), assembly_order, director_hints}` (fixture or LLM).
2. **Build** — `pipeline/blender/spatail_build_from_plan_driver.py` (headless) builds the plan, enriches the registry with `assembly.offsets` + `camera_presets`, exports the GLB. Wrapped by `intelligence/generative_bridge.py`.
3. **Register** — `asset_library.register_generated_asset()` (in-memory + persisted manifest). No source edit, no server restart needed.
4. **Stage** — `walkthrough.build_walkthrough(manual_text, mode="generate")` → explode/assemble beats + `scene.assembly` + `meta.camera_presets`.
5. **Refine** (optional) — `spatail_mesh_select` / `spatail_mesh_region` for sub-mesh regions before/after export.

The lower-level primitive builder (`spatail_model_from_primitives.py`) and the
manual register-and-wire flow below are still valid for hand-authoring a curated
asset, but the automated path above supersedes them for dropped manuals.

## Build-plan schema

```json
{
  "assetId": "shelving_unit",
  "kind": "flat-pack shelving unit",
  "units": "cm",
  "up_axis": "z",
  "parts": [
    {"name": "side_left", "role": "side_panel",
     "aliases": ["left side", "left panel"],
     "primitive": "box", "size": [2, 30, 86], "location": [-39, 0, 45]}
  ],
  "groups": [{"group_id": "frame", "members": ["side_left", "side_right"]}],
  "assembly_order": ["bottom", "side_left", "side_right", "top"],
  "director_hints": { "asset_kind": "...", "narration_tone": "instructional" }
}
```

Primitives: `box {size:[x,y,z]}`, `cylinder {radius, depth, axis}`,
`tube {radius, inner_radius, depth, axis}`. **Names are semantic** (the manual
says "the left side panel", not "mesh3") and **aliases drive step resolution** —
the walkthrough maps manual nouns onto parts through them, so list the words a
manual would actually use.

**Real CAD instead of primitive blocks:** parts can be upgraded to genuine
parametric geometry (build123d / OpenCascade fillets, tubes, holes, brackets) via
[[spatail-cad-import]]. It runs a CAD pre-stage in the text-to-cad venv, bakes each
part to a metres / Z-up / centred mesh payload, and `build_from_plan(cad_manifest=…)`
seats it by `location` exactly like a primitive — additive, so any part the CAD stage
can't make falls back to its primitive. Same size, orientation, and placement; far
more accurate geometry.

## Build (Blender)

```python
import sys; sys.path.insert(0, r"C:/SPATAIL_MAX/pipeline/blender")
import importlib, json, spatail_model_from_primitives as mp; importlib.reload(mp)
plan = json.load(open(r".../shelving_unit.plan.json"))
res = mp.build_from_plan(plan, make_active=False)   # builds into scene SPATAIL_<assetId>
json.dump(res["registry"], open(r".../shelving_unit_part_registry.json", "w"), indent=2)
```

- **Non-destructive**: builds into a dedicated scene; the open scene stays in front (`make_active=False`).
- Geometry is built via `bmesh` data (no operators), so no active-scene/context juggling.
- Emits a registry in the shape walkthrough.py consumes: `parts{name:{role,aliases}}`, `aliases{}`, `kinematicGroups[]`, `engine_bbox`, `assembly_order`, `director_hints`. bbox uses `matrix_basis` (the new scene isn't the active view layer, so `matrix_world` is unevaluated).

## Export GLB (meters, Y-up, overlays included)

The pipeline/web run in meters and Y-up. Author in cm, then scale at export:

```python
# in the built scene, temporarily: o.location *= 0.01; o.scale = (0.01,)*3
bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB",
    use_selection=True, use_active_scene=True,
    use_visible=False,        # CRITICAL: include hidden region-overlay meshes
    export_apply=True, export_yup=True, export_cameras=False)
# then reverse: o.scale=(1,)*3; o.location *= 100
```

Gotchas learned: the glTF exporter **skips hidden objects unless `use_visible=False`**
(region overlays are baked hidden), and OpenGL preview renders need
`view_context=False` to use the scene camera instead of the viewport view.

## Register + wire

- `asset_library.py`: add a `LibraryAsset(asset_id, kind, keywords[...], glb, registry, animation_library, blend=None)`. `asset_id` MUST equal the web ASSETS key. Give an empty `{"animations": {}}` animation library for static assemblies. **Restart the intelligence server (5175)** — it holds the library in memory.
- `main.js` ASSETS: add `{ glbUrl, regionsUrl, placeholder, cameraOverride }`. Generated assets need their own camera distances (a 0.9 m unit ≠ a 0.06 m fan). `index.html`: add a switcher `<button data-asset="...">`.

## Stage the demo

`build_walkthrough(manual_text)` → ingest (LLM) classifies + extracts steps →
`match_product` now hits the registered asset → `_stage_step` maps each step's
`action` + `target_parts` to a beat (highlight + label + dim + camera; assembly
actions mount/connect/slide/press are static focus). Result: a playable demo
with the existing step rail + transport (next/prev via `player.scrubToBeat`).

## Anti-goals

- ❌ Touch the user's open scene — always build into the dedicated `SPATAIL_<assetId>` scene.
- ❌ Use `meshN` names — generated parts are semantic; the manual refers to them by name/alias.
- ❌ Export without `use_visible=False` — region overlays (hidden) silently drop out of the GLB.
- ❌ Forget to restart the 5175 server after editing `asset_library.py`.
- ❌ Try to model fine surface detail with primitives — build the base, then refine with [[spatail-mesh-select]].
