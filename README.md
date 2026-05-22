# SPATAIL — Replit for Spatial Experiences

SPATAIL is a **spatial experience engine**. Given a prompt and supporting
content (cards, files, CAD), it decides:

- *what should appear* — readable text, 3D model, exploded view, callout, decision card
- *where it should live* — wall, table, floor, anchored to a real object, in user hand-reach
- *how large it should be* — readable at 1m/2m/3m, true scale, tabletop scale, room scale
- *how users should interact* — explode, select, focus, advance, expand
- *how attention should be guided over time* — ordered attention plan with narration

The output is one **`SpatialExperienceContract.json`** that the web viewer
renders today and that the visionOS player will render tomorrow.

> **The product principle:** SPATAIL does not force everything into 3D.
> Numbers, summaries, and lists stay as readable 2D panels. Physical
> parts become 3D models, exploded views, and anchored callouts. Time
> becomes a walkable floor path. Decisions float in hand-reach.

See [docs/SPATAIL.md](docs/SPATAIL.md) for the architecture and the
mapping from the SPATAIL one-pager to the code.

## What's in here

This repo also contains the **v0.1 CAD ingestion pipeline** — the
original local loop that scans `/assets_raw`, normalizes 3D / CAD files
via Blender, and emits a legacy `SpatialSceneContract.json`. SPATAIL uses
that pipeline as one of several input sources for spatial experiences.

- one shared **Spatial Experience Contract** drives every surface
- the contract is generated from real content + assets, not invented
- the same element definitions render in the web viewer today and the
  visionOS player tomorrow
- Claude (as system architect) inspects content, infers intent, and
  writes the contract; the viewer is a thin consumer

## What's in here

```
SPATAIL_MAX/                   (this repo's root)
├── assets_raw/                # drop 3D / CAD files here (legacy ingestion)
├── assets_processed/          # normalized .glb output (legacy ingestion)
├── demos/                     # SPATAIL card JSONs (prompt + content)
├── scene_contracts/           # all generated contracts live here
├── pipeline/
│   ├── spatail/               # the SPATAIL five-layer pipeline
│   ├── spatail_generate.js    # CLI: cards -> SpatialExperienceContract
│   ├── scanner.js, classifier.js, blender_runner.js, contract_builder.js,
│   └── generate.js            # legacy CAD pipeline (still works)
├── blender_tools/             # headless Blender Python for normalization
├── viewer/
│   ├── spatail.{html,js,css}  # SPATAIL viewer (default at /)
│   └── index.html, viewer.js  # legacy CAD viewer
├── figma_tools/               # placeholder for Figma → UI brick flow
├── visionos_export/           # contract consumer (Vision Pro runtime, v0.2)
└── docs/
    ├── SPATAIL.md             # architecture + doc-to-code mapping
    └── GETTING_STARTED.md     # legacy run instructions
```

## Quick start

```bash
npm install
npm run spatail        # generate spatial contracts from demos/*-card.json
npm run viewer         # serve http://localhost:5173/
```

Open `http://localhost:5173/` — the picker switches between the two
bundled demos:

- **Mustang Service Assistant** — proves the "do not force 3D" rule:
  status / insurance / history / tools as 2D panels, air-filter housing
  as a highlighted target on the table, exploded assembly aligned
  directly above it, clips / Torx / dirty-filter as anchored callouts.
- **Q3 Manufacturing Cost Review** — proves the multi-mode mix: KPIs on
  the wall, factory process on the table, Q3 events as a walkable floor
  timeline, recommended actions as floating decision cards.

The legacy single-CAD viewer is still at `/viewer/index.html` and the
legacy pipeline still runs via `npm run generate`.

## iPhone AR preview — `SPATAILMobileAR/`

The first SPATAIL platform target is an **iPhone AR prototype**, not
Vision Pro. The Vision Pro design intent is preserved: content lives
in real space, 2D panels are placed spatially (not as phone UI), 3D
objects are anchored in the environment. iPhone is the demo surface;
the visionOS player consumes the same contracts later without changes.

The iOS app lives in `SPATAILMobileAR/` and is built with SwiftUI +
ARKit + RealityKit (MVVM). It bundles the generated contracts as
resources and renders one element per `representationMode` — panels,
wall dashboards, tabletop models, floor timelines, anchored callouts,
exploded views (Mustang air filter), highlighted objects, and
floating decision cards.

See [SPATAILMobileAR/README.md](SPATAILMobileAR/README.md) for the
Xcode setup. To keep the bundled contracts fresh:

```bash
npm run spatail:ios   # regenerate + copy into SPATAILMobileAR/Resources/
```

This scaffold was authored on Windows and has not been compiled. Open
on a Mac with Xcode 16.3+, run `xcodegen generate`, and run on a
physical iPhone (ARKit does not run in the simulator).

See [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) for the full walk-through,
what's wired vs stubbed, and where to drop your own models.

## Status

| Capability                                                            | Status |
| --------------------------------------------------------------------- | ------ |
| **SPATAIL — content → spatial experience**                            |        |
| ContentIngestionLayer (card JSON, files, CAD asset groups)            | ✅      |
| SpatialUnderstandingLayer (domain + per-source content types)         | ✅      |
| RepresentationSelector (2D vs 3D vs timeline vs anchored vs floating) | ✅      |
| SpatialPlacementEngine (wall / table / floor / object / hand-reach)   | ✅      |
| SpatialExperienceContract (schema + per-element reasoning)            | ✅      |
| Mustang demo (target + exploded view alignment)                       | ✅      |
| Q3 demo (multi-mode mix)                                              | ✅      |
| Viewer renders every representation mode with reasoning sidebar       | ✅      |
| Real GLB loading inside SPATAIL elements (vs placeholder boxes)       | 🔲 v0.2 |
| **Legacy CAD ingestion**                                              |        |
| Scan `/assets_raw` for 3D files                                       | ✅      |
| Normalize STL / OBJ / FBX / USD / GLB → GLB                           | ✅      |
| Normalize STEP / STP / IGES → GLB                                     | ⚠️ requires Blender extension |
| Legacy `SpatialSceneContract.json` + bricks viewer                    | ✅      |
| **Runtime targets**                                                   |        |
| visionOS player                                                       | 🔲 v0.2 |
| Figma-driven UI layout                                                | 🔲 v0.2 |
