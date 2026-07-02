---
name: create-spatail-asset
description: >-
  Creates a normalized, XR-ready SPATAIL 3D asset by running the AI Asset Factory.
  Ingests a raw AI-generated 3D model (GLB/GLTF/OBJ/FBX/STL from Meshy, Tripo, Rodin,
  Luma, etc.), inspects it, conservatively cleans it, normalizes it to fixed bounds
  (default 1x1x1 m, bottom-center origin), and exports a normalized GLB + RealityKit
  USDZ + preview image + report — then can publish it to the SPATAIL library and open
  a live web viewer. Use when the user wants to "create a SPATAIL asset", "make an
  asset", "normalize/ingest/clean a 3D model", "run the asset factory", "make an
  XR-ready or USDZ model", or "view a normalized asset live".
---

# Create a SPATAIL asset

Drive the SPATAIL **AI Asset Factory** to turn a raw AI-generated 3D model into a
clean, fixed-bounds, XR-ready asset (normalized GLB + USDZ + preview + report), and
show it live. Follow these steps. Keep the user-facing conversation in plain language.

> **Prefer the MCP tools when available.** This plugin ships a local MCP server
> (`spatail-asset-factory`) that runs the LOCAL Blender for you. If tools named
> `create_asset`, `list_assets`, `get_asset`, `publish_asset`, `start_viewer` are
> available, use them instead of the shell commands below — e.g. call `factory_status`
> first to confirm the repo + Blender are found, then `create_asset` (with a model
> path, or `demo=true`). The shell steps below are the fallback when the MCP server is
> not connected.

## 1. Locate the factory and Blender

The factory lives in a SPATAIL repo at `asset_factory/` with these entry points:
`asset_factory/worker_manager.py`, `asset_factory/publish_to_spatail.py`,
`asset_factory/web_viewer.py`.

- From the working directory, check that `asset_factory/worker_manager.py` exists
  (search the cwd and one or two levels up). If it is not found, ask the user to open
  their SPATAIL repo (the folder containing `asset_factory/`) and run there.
- The factory needs **Blender 5.1** headless. It is found via the `BLENDER_EXE`
  environment variable, else the default `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`.
  If Blender is not present, tell the user it is required and stop.

All commands below run from the SPATAIL repo root.

## 2. Get the input model

Determine what to create from, in this order:

1. **A model the user provided** (a dropped/attached or referenced `.glb`, `.gltf`,
   `.obj` (+`.mtl`), `.fbx`, or `.stl`, optionally with a `textures/` folder): create
   an asset group folder `assets_raw/<asset_id>/` (slugify a short name from the file
   or the user's description) and copy the model (and its textures/mtl) into it.
2. **An existing group** already under `assets_raw/` the user names: use it as-is.
3. **No input at all** ("just make one" / a demo): seed a recognizable model from the
   repo's library so the factory has something to create. Copy one
   `public/assets/spatail-library/<category>/<name>.glb` into
   `assets_raw/<asset_id>/model.glb` (e.g. `mechanical/gear_large.glb`,
   `astronomy/earth.glb`, `architecture/colosseum.glb`). Tell the user it is a demo
   seed and they can drop their own AI-generated model next time.

> Generating a brand-new mesh from a text description (e.g. "a coffee mug") is NOT
> part of the factory — that needs an AI 3D generator. If the user asks for that, see
> `references/asset-factory.md` ("Generating new meshes") and offer the options there.

## 3. Run the factory

Process the group(s) into normalized GLB + USDZ:

```bash
python asset_factory/worker_manager.py --input assets_raw --output assets_processed --export-usdz --only <asset_id>
```

- Drop `--only <asset_id>` to process every group in `assets_raw/`.
- Each asset runs in its own isolated Blender process; one failure never aborts the
  batch. The pipeline is: import → inspect → conservative cleanup → fixed-bounds
  normalize → origin normalize → preview → export GLB+USDZ → manifest/report → validate.
- For larger batches add `--max-workers N`; for a slow asset add `--asset-timeout-seconds 900`.

## 4. Read and report the result

After it runs, read `assets_processed/<asset_id>/asset_manifest.json` and report in
plain language:

- **status** (success/failed),
- **final size** (`final_bounds_m`) vs **target** (`target_bounds_m`) and whether it
  fits (it should — the factory guarantees it),
- **bounds_mode** / **origin_mode**, **triangle_count**, and the exported files.

Also read `assets_processed/batch_report.json` for the batch summary. If status is
`failed`, read `asset_report.json` (its `errors`) and explain what went wrong (common
causes: corrupt/unsupported file, no mesh, Blender not found).

Then **show the preview**: display `assets_processed/<asset_id>/preview.png` so the
user can see the real normalized model.

## 5. Show it live (offer)

Offer to view the asset live in 3D / AR (no app build):

```bash
python asset_factory/web_viewer.py
```

This serves a `<model-viewer>` gallery of every processed asset. Give the user the
local URL (`http://127.0.0.1:8790/`) and, if Tailscale is up, the Tailscale URL
(`http://<pc>.<tailnet>.ts.net:8790/`) to open on a phone — drag to orbit, tap
**"View in AR"** on iPhone for AR Quick Look with the USDZ. The viewer auto-lists
whatever is in `assets_processed/`, so refreshing picks up new assets.

## 6. Publish into the SPATAIL app (optional)

If the user wants the asset available inside the SPATAIL iOS app's Representation
flow, publish it into the library under a subject:

```bash
python asset_factory/publish_to_spatail.py --asset-id <asset_id> --subject "<a short subject, e.g. 'a gear'>"
```

This copies the GLB+USDZ into the served library and registers it so a matching
prompt resolves to it. It prints the exact phone steps and the Tailscale URL.

## Notes

- Default config: target bounds 1×1×1 m, `fit_longest_axis`, `bottom_center` origin,
  preview on, USDZ via `--export-usdz`. To change bounds/origin per asset, drop an
  `asset_factory_config.json` in the group folder — see `references/asset-factory.md`.
- Everything is regenerable: `assets_processed/` is rebuildable from `assets_raw/`.
- Full config reference, bounds/origin modes, troubleshooting, and the
  generate-new-meshes options are in `references/asset-factory.md` — read it when the
  user asks about config, custom bounds, failures, or generating from a text prompt.
