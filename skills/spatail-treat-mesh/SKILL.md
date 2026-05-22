---
name: spatail-treat-mesh
description: Generic mesh treatment — given any CAD file (OBJ/STL/FBX/GLB), produce a fully-studied, normalized scene plus a treatment manifest JSON that downstream rig/animate/visualize code consumes. Always run this BEFORE attempting to rig or animate a new asset. Asset-agnostic — works for engines, wheels, furniture, anything.
when_to_use: When a new CAD file lands and needs to be brought into the SPATAIL pipeline. Or when an existing scene has gotten messy and needs a reset. Or when downstream rigging code is fighting wrong pivots / origins.
---

# Mesh Treatment Skill

## What this exists for

Everything downstream (rigging, animation, visualization) needs to **assume things about the mesh**: that each part is a separate object, that origins are at meaningful pivots, that transforms are clean, that we know the principal axes of every part. Without that, every animator winds up doing its own ad-hoc inspection — and they all do it slightly differently, producing inconsistent rigs.

Mesh treatment is the **one canonical place** that prepares geometry for downstream use. It runs once per asset (or per re-import), produces a `<asset>.treatment.json`, and *that's* what rig/animate code reads.

## The 7 stages

### 1. Import & sanitize
- If raw file provided: import it cleanly into a fresh Blender scene.
- If already in scene: optional pass to verify nothing has dangling parents or weird transforms.
- Apply all transforms (location, rotation, scale → identity, geometry baked into mesh data).
- Confirm mesh data lives in world coordinates as expected.

### 2. Topology audit
For each object:
- Vertex / edge / face counts.
- Manifold check (non-manifold edges, holes).
- Mesh island count (does this "single object" actually contain multiple disconnected parts?).
- World-space bbox + size.
- Units sanity: estimate physical scale (cm vs mm vs m) from extents.

### 3. Segmentation
- Run `mesh.separate(type='LOOSE')` on each object that contains multiple islands → one Blender object per physical part.
- Re-link separated objects to the same collection.
- Result: every distinct rigid body in the scene is its own object.

### 4. Principal geometry (per part)
For each segmented part, via PCA on its vertices:
- Centroid (vertex mean — biased toward dense features, but useful).
- Bbox centre (mass-uniform).
- Three principal axes + eigenvalues.
- Aspect ratio (eigenvalue ratios → elongated / planar / blob).
- Elongation classification:
  - `rod-like`: eig0 ≫ eig1 ≈ eig2 (one long axis dominates)
  - `disc-like`: eig0 ≈ eig1 ≫ eig2 (planar)
  - `cylinder-like`: eig0 ≫ eig1 ≈ eig2 AND has rotational symmetry around eig0
  - `blob`: eigenvalues comparable

### 5. Pivot detection (the critical step)
For each part, determine its **natural pivot point** based on its shape class:
- `rod-like` → one of its two endpoints (whichever has smaller cross-sectional spread = small end). For a connecting rod that's the wrist-pin eye.
- `cylinder-like` → centre of one of the two circular faces along the principal axis (depends on which mating end the part rotates around).
- `disc-like` → centre of the disc.
- `blob` → bbox centre.

This is what gets baked into the next stage.

### 6. Origin normalization
For each part: shift its mesh data so the object origin lands at the detected pivot point, while geometry stays in the same world position. Now `obj.matrix_world.translation == pivot` for every part — any future rotation constraint or parenting operation pivots around the correct point.

### 7. Treatment manifest
Write one JSON: `<asset>.treatment.json` containing:
- The full per-part audit (topology, PCA, pivots).
- The normalization log (which origins were moved, by how much).
- The shape classifications.
- Asset-level summary (part count, bbox, units, suggested scale).
- Schema version + timestamp.

## Reusability

The skill knows nothing about engines. It works on:
- A V10 engine → identifies rods, pistons, throws by shape class, sets each origin to its pivot.
- A steering wheel → identifies rim, grips, spokes, center hub.
- A chair → identifies seat, legs, back, armrests.
- Any segmented CAD.

Downstream skills (mechanism-specific rigging like `spatail-rig-four-stroke`) consume the manifest, never the raw mesh.

## How to invoke

```python
exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_treat_mesh.py").read())
treat_mesh(
    source=r"path/to/asset.obj",                # or None if already in scene
    asset_id="v10_engine",
    out_dir=r"C:/SPATAIL_MAX/assets_processed/treated/v10_engine",
)
```

Returns the manifest dict; writes `<out_dir>/<asset_id>.treatment.json`.

## Stage outputs

Each stage writes a section into the manifest under a stable key:
- `import` — source path, import time, scale baking log.
- `topology` — per-object audit.
- `segmentation` — split log (what got separated).
- `principal_geometry` — per-part PCA.
- `pivots` — per-part detected pivot + classification reason.
- `normalization` — per-part old origin → new origin.
- `summary` — asset-level rollup.

Downstream rigging code reads the `pivots` + `principal_geometry` sections to build kinematic chains.

## Anti-goals (what this skill does NOT do)

- ❌ Identify "this is a piston, that's a rod" semantically. That's the **labelling** pass (visual inspection, separate skill).
- ❌ Decide what motion to animate. That's the **mechanism** pass (per-asset, separate skill).
- ❌ Render anything. Rendering is downstream.
- ❌ Touch materials or shading. Treatment is geometry-only.

These boundaries keep the skill scalable across asset types — the moment you bake engine-specific assumptions in here, it stops being reusable.
