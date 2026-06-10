# SPATAIL Asset Factory — Cowork plugin

Create XR-ready SPATAIL 3D assets from inside Cowork. The plugin drives the repo's
**AI Asset Factory**: drop in a raw AI-generated 3D model (Meshy / Tripo / Rodin /
Luma — GLB/GLTF/OBJ/FBX/STL), and it cleans it, normalizes it to fixed bounds, and
exports a normalized **GLB + USDZ + preview**, which you can view live in 3D/AR.

## What it adds

- A **local MCP server** — `spatail-asset-factory` — that **runs the Blender on your
  PC**. Cowork launches it locally (stdio), and it exposes tools that drive the
  factory: `factory_status`, `create_asset`, `list_assets`, `get_asset`,
  `publish_asset`, `start_viewer`, `stop_viewer`. Because the server process runs on
  your machine, it always uses your local Blender — even if Cowork itself is remote.
- A **skill** — `create-spatail-asset` — that you trigger by asking things like:
  - "create a SPATAIL asset from this model"
  - "normalize / ingest / clean this GLB (or OBJ / FBX / STL)"
  - "run the asset factory"
  - "make a USDZ / XR-ready model and show me"
  - "just make me a demo asset"

The skill ingests your model → runs the factory → reports the result (size, fits the
target bounds, triangle count) → shows the preview → and can open the **live web
viewer** (3D + iPhone AR Quick Look) and/or publish the asset into the SPATAIL app.

## Requirements

- The **SPATAIL repo** on the PC (the folder containing `asset_factory/`). The MCP
  server finds it via the `SPATAIL_REPO` env var in `.mcp.json` (default
  `C:\SPATAIL_MAX`) — edit that if your repo lives elsewhere.
- **Blender 5.1** installed (found via `BLENDER_EXE`, or the default install path).
- **Python 3.10+** with the `mcp` package: `pip install mcp`. The server is launched
  with `python` — make sure that resolves to an interpreter where `mcp` is installed
  (or change the `command` in `.mcp.json` to that interpreter's full path).

## Install

In Cowork, install the `.plugin` from the plugin manager (or accept it from chat).
Cowork starts the local MCP server automatically; then just ask Claude to create or
normalize an asset (e.g. "make me a demo SPATAIL asset" or "normalize this GLB").

To run the local server by hand (sanity check):

```bash
SPATAIL_REPO=C:\SPATAIL_MAX python <plugin>/server/spatail_factory_mcp.py   # speaks MCP over stdio
```

## Not included

Generating a brand-new mesh from a text description is not part of the factory (it
normalizes existing models). The skill documents how to wire a generator (Meshy, or
local Blender object generation) if you want that — see the skill's reference notes.
