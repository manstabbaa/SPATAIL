# Getting started

This is the v0.1 local loop. Five minutes, one model on screen, all four
interaction bricks working, and a clean Spatial Scene Contract you can read.

## Prerequisites

| Tool   | Version | Why                                              |
| ------ | ------- | ------------------------------------------------ |
| Node   | ≥ 18    | runs the pipeline + viewer                       |
| Blender| 4.1+    | normalizes any source format to GLB; also reads dimensions, materials, hierarchy |

On Windows the pipeline auto-discovers Blender at:

```
C:/Program Files/Blender Foundation/Blender 5.1/blender.exe
```

(and several nearby versions). Override with the `SPATIAL_BLENDER` env var if
yours lives elsewhere.

If Blender is missing, the pipeline still runs — but it only handles formats
the browser can load directly (`.glb`, `.gltf`, `.obj`, `.stl`). CAD formats
will be marked `failed` with a clear reason.

## Step 1 — Drop your model

Put a 3D file in `assets_raw/`. Supported formats:

- **Browser-native:** `.glb`, `.gltf`, `.obj`, `.stl` *(no Blender needed)*
- **Blender-normalized:** `.fbx`, `.ply`, `.usd`, `.usda`, `.usdc`, `.usdz`
- **CAD via Blender extension:** `.step`, `.stp`, `.iges`, `.igs` *(see "STEP support" below)*

You can drop:
- a single file (`my_engine.glb`)
- a folder (`my_engine_assembly/` containing many parts)

Files inside one folder are treated as **one logical asset group** — that's
how the lego minifig, the bearing, etc. work in the bundled samples.

## Step 2 — Run the pipeline

```bash
npm install        # no-op for now (zero deps), but kept for discoverability
npm run generate
```

What this does:

1. Walks `assets_raw/` and lists supported files
2. Groups them per top-level folder
3. Picks the first group as the active scene
4. Runs Blender (headless, `--background`) on each file to:
   - import using the right operator
   - measure object count, vertex count, face count, bounding box
   - export a normalized `.glb` to `assets_processed/`
   - write a sidecar `*.analysis.json`
5. Classifies the group (domain, primary object, likely use case)
6. Writes `scene_contracts/SpatialSceneContract.json`

You'll see one line per asset:

```
[generate]   [1/4] analyze: futuristic-...\CAD\ADJ.stl
...
[generate] wrote scene_contracts/SpatialSceneContract.json  (4/4 assets processed)
```

If something can't be imported, the run continues and the contract records
the failure. Check `assets_processed/<id>.analysis.json` for the reason.

## Step 3 — Run the viewer

```bash
npm run viewer
```

Then open <http://localhost:5173/>. You'll see:

- the model, auto-framed, with orbit controls (drag to rotate, wheel to zoom)
- a sidebar showing the asset inventory, story sequence, and spatial understanding
- four UI buttons (the **interaction bricks** from the contract):
  - **Reset View** — restores camera, materials, visibility, positions
  - **Highlight** — primary object turns blue (toggle)
  - **Isolate** — non-primary parts are hidden (toggle)
  - **Explode** — components spread outward from the scene center (toggle)
- the live `bbox` and part count in the bottom-left HUD
- a link to the raw `SpatialSceneContract.json` in the footer

## Step 4 — `npm run dev`

```bash
npm run dev
```

Equivalent to `npm run generate && npm run viewer`. Use this when you've just
dropped a new model and want to see it.

## What the contract looks like

`scene_contracts/SpatialSceneContract.json` is the **single source of truth**.
The web viewer reads it. The future visionOS player will read the same file.
The Figma plugin will write into the same `uiElements` array.

Skim it once. The fields you'll care about:

- `sceneName`, `version`, `createdAt`
- `assets[]` — inventory with `role: primary_object | component | ...`
- `assetAnalysis[]` — Blender-derived metrics per asset
- `spatialUnderstanding` — `detectedDomain`, `primaryObject`, `likelyUseCase`, `representationMode`
- `placement` — anchor, scale mode, safe distance
- `orientationRules` — axis convention
- `interactionBricks[]` — what the scene *can do*
- `uiElements[]` — what the user *sees*, with `visibleIn: ["viewer", "vision_pro"]`
- `storySequence[]` — guided tour steps
- `validationRules[]` — soft schema checks

## STEP / STP support

Blender 5.1 doesn't ship the STEP importer by default — it's a separate
extension on the Blender Extensions Platform.

To enable it:

1. Open Blender once (GUI)
2. Edit → Preferences → Get Extensions
3. Search "STEP"
4. Install "STEP Importer" (or any compatible CAD importer addon)

After that, `npm run generate` picks up the operator automatically — the
pipeline tries known addon module names (`io_import_step`, `io_scene_step`,
`bl_ext.user_default.step_importer`, etc.) before each STEP/STP import.

Until then, STEP files in `assets_raw/` are recorded as `failed` with the
reason in their `*.analysis.json`. The viewer simply skips them.

## What's stubbed

| Area                                | State |
| ----------------------------------- | ----- |
| Blender MCP integration             | Pipeline uses headless CLI; MCP is available to the Claude agent for live iteration but not required for the build |
| STEP / STP / IGES                   | Importer probe works, but requires the Blender STEP extension to actually convert |
| `figma_tools/`                      | Folder + README; the Figma → uiElements bridge is v0.2 |
| `visionos_export/`                  | Folder + README explaining contract consumption; no Swift code yet |
| Multi-scene picker                  | Pipeline currently selects the first asset group; multi-scene viewer is v0.2 |
| Scale normalization                 | Viewer auto-frames so mm/m mismatch is invisible; explicit normalization in Blender is v0.2 |
| Material remapping                  | Pass-through; the viewer overrides primary-object material when Highlight is on |

## Next best step

The next thing that moves the product forward is the **visionOS player**:
a SwiftUI + RealityKit app that consumes the same `SpatialSceneContract.json`,
loads the same `assets_processed/*.glb`, and renders the same buttons.
See `visionos_export/README.md`.
