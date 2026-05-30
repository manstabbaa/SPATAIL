---
name: spatail-usermanualxr
description: Turn any product user manual into a step-by-step XR walkthrough. Drop a manual (text/PDF) and the system classifies the product, matches a 3D model from the SPATAIL asset library, ingests the manual into ordered steps, bakes any motions the model is missing, and stages a linear walkthrough contract the web runtime plays beat-by-beat with a step rail. Use when you want a "drop a manual → get a guided 3D demo" experience, or when adding a new product to the usermanualXR library.
when_to_use: When a user wants to convert a user manual / quick-start guide / assembly instructions into a spatial walkthrough, OR when extending usermanualXR to support a new product (register its model in the asset library + author its registry with kinematicGroups + director_hints).
---

# usermanualXR

## What it does

```
                          ┌─ MATCH  → curated library model → bake missing motions ─┐
drop a manual → classify ─┤                                                          ├→ stage → play in XR
                          └─ GENERATE → segment into parts → BUILD part-by-part ─────┘   (step rail + 3D)
```

Two modes share the back half (stage → contract → play):

- **match** (`mode="match"`): classify the product and reuse a curated model
  (fan, engine). The original explainer-drawer path.
- **generate** (`mode="generate"`, the default for `/api/manual`): when no
  curated model fits, an agent SEGMENTS the manual into parts + steps, the
  product is BUILT part-by-part in headless Blender, registered, and staged as
  an explode→assemble walkthrough. This is the **Manual → XR mini-app**
  (`web/manual.html`). See [[spatail-model-from-manual]] for the build half.

A user manual is text + diagrams describing how to use/assemble/maintain a
product. usermanualXR turns that document into a guided 3D walkthrough:
one beat per manual step, with the product highlighted, labelled, and
animated as each step describes.

This is the **second mode** of the SPATAIL EngineExplainer runtime. The
first mode is free-form Q&A ("how does a piston work?"); this mode is a
linear procedure driven by a document.

## The pipeline (create parts → animate → assemble)

### 1. Ingest — `intelligence/manual_ingest.py`
A Sonnet agent reads the manual text and returns a `manual_plan`:
`product_kind`, `product_keywords`, `title`, and ordered `steps[]`. Each
step has a normalized `action` verb (identify / mount / connect /
power_on / verify / clean / remove / slide / press / rotate / none),
free-text `target_parts`, an optional `spec` and `warning`.

### 2. Match — `intelligence/asset_library.py`
`match_product(kind, keywords)` scores the classified product against the
registered library (`ASSET_LIBRARY`) and returns the best `LibraryAsset`
(its GLB, part registry, animation library, authoring blend). Keyword +
kind scoring now; embeddings later without changing callers.

### 3. Create parts / animate — reuses the whole SPATAIL stack
The matched asset already went through the build pipeline:
`spatail-blender-director` (centered pivots + rest poses) →
`spatail-animate-from-rest` (baked clips) →
`spatail-motion-validator` (PASS gate). Its registry declares
`kinematicGroups` (rotor, crankshaft, …) with `driven_by_action`.

If a manual step needs a motion the model's animation library does NOT
contain, the walkthrough emits a `bake_animation` action and the
**bake bridge** (`intelligence/bake_bridge.py`) runs Blender headlessly to
bake it + re-export the GLB before the contract ships.

### 4. Stage / assemble — `intelligence/walkthrough.py`
`build_walkthrough(manual_text)` maps each step → one contract beat using
a deterministic action→staging table (see `prompts/walkthrough_director.md`
for the same table the agentic variant would use). It resolves
`target_parts` to mesh ids via the registry aliases / kinematic groups,
honors the asset's `director_hints` (cream background, allowed camera
presets, dim range), and produces a `steps_index` the web step rail
renders.

### 5. Serve / play
`POST /api/manual {manual_text}` →
`{ ok, asset_id, asset_glb, manual_plan, match, contract }`. The web shell
swaps to the matched model, renders the left step rail, and plays the
walkthrough; clicking a step scrubs the 3D to that beat.

## How to invoke (programmatic)

```python
from engineexplainer.intelligence import walkthrough
result = walkthrough.build_walkthrough(open("manuals/fan_user_manual.txt").read())
# result["asset_id"] == "fan"; result["contract"] is playable
```

Or via HTTP:

```bash
curl -X POST http://localhost:5175/api/manual \
  -H 'Content-Type: application/json' \
  -d "{\"manual_text\": \"<paste manual>\"}"
```

Or in the web UI: click **Manual → XR** (bottom-left) — it opens the dedicated
mini-app (`web/manual.html`). Drop a `.txt/.md/.pdf` or paste text (or "Load
KALLAX sample"), **Build walkthrough**. The mini-app defaults to `generate`.

## Adding a new product to the library

1. Build the model through the SPATAIL pipeline (treat-mesh →
   blender-director → animate-from-rest → motion-validator PASS).
2. Author `engine/<asset>_part_registry.json` with `aliases`,
   `kinematicGroups` (each with `driven_by_action`), and `director_hints`.
3. Author `engine/<asset>_animation_library.json`.
4. Register it in `asset_library.ASSET_LIBRARY` with kind + keywords +
   paths.
5. Add it to the web `ASSETS` registry (main.js) with its cameraOverride.

That's it — any manual that classifies to that product kind now routes to
the new model.

## Anti-goals

- ❌ Generate 3D geometry from manual *diagrams/images*. (Still out of scope —
  the generate mode builds from the manual's TEXT via a segmented primitive
  plan, not by interpreting drawings. An image→3D spike could add that later.)
- ❌ Merge multiple manual steps into one beat. One step = one beat;
  procedures must stay legible.
- ❌ Free-form Q&A. That's the other runtime mode (the Q&A director).
- ❌ Asset-specific staging hard-coded in walkthrough.py. Per-asset
  behavior lives in the registry's `director_hints`.

## Composition / where it sits

```
Q&A mode:           prompt → mechanic → director → critic → semantic → contract
usermanualXR match: manual → ingest  → match     → walkthrough(stage) → bake → contract
usermanualXR gen:   manual → segment → build(Blender) → register → walkthrough(stage) → contract
                                                          ↓
                              (all) → bake/build-bridge → web runtime → motion-validator
```
