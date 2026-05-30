---
name: spatail-blender-director
description: Per-mesh pivot centering + rest-pose data sheets. Walks every mesh in the scene, moves its origin to its own bounding-box center (so the part rotates and scales around itself), and writes one JSON per part recording the rest_transform (location, rotation, scale, world matrix), the local geometry (bbox, size, principal axis, vertex/face counts), the original origin before centering, and nearest-neighbor distances. The per-part files are the modular foundation animation, layout, and the web runtime all read from — any mesh can be "placed back" exactly where it belongs by applying its recorded rest_transform.
when_to_use: After spatail-treat-mesh has segmented the asset into one Blender object per physical part, OR when an existing scene has inconsistent pivots that downstream animation can't reason about. Run before spatail-animate-engine / spatail-rig-engine when you want every part centered on itself rather than at a shape-class-specific "natural pivot" (rod endpoint, journal center, etc). The two pivot strategies are complementary: treat-mesh picks semantic pivots good for kinematic chains, blender-director picks uniform geometric centers good for self-rotation, billboard halos, and per-part modular animation.
---

# Blender Director

## Why this exists

The animations that ship with the engine asset have a recurring bug: parts
swing through space because their origin is somewhere other than the part
itself. A "piston" whose origin is at the world origin rotates around the
world origin, not around the piston. A crank throw whose origin is at the
crank axis is fine for crank rotation but useless if you want to highlight
the throw with a halo at its center — the halo lands at the crank axis, not
on the throw.

Different downstream uses want different pivots:

  - **Slider-crank animation** wants the crank's pivot at the crank axis
    and the rod's pivot at the small-end (treat-mesh's choice).
  - **Per-part visual reasoning** (halos, labels, frame_on cameras,
    "rotate this part to show it off") wants the pivot at the part's own
    geometric center — that way "this part" maps cleanly to "this pivot."

This skill enforces the **geometric-center** convention uniformly, and
writes a data sheet per part that lets *any* mesh be placed back exactly
where it belongs. Once that's true, animation, framing, and layout all
become trivially modular: each part has a single source of truth for
its rest pose, and any motion is expressed as a delta from that rest.

## What it does, mesh by mesh

For every Mesh object in the scene (you can also restrict by collection
or name prefix):

1. **Snapshot the original pose.** Capture `matrix_world` *before*
   touching anything. This is the "rest matrix" that gets written to the
   JSON — it is the contract the runtime trusts.

2. **Compute the bbox center in the mesh's current local frame.** That
   point becomes the new origin.

3. **Shift mesh data by `-local_center`** so the geometry is now centered
   on the local origin. This is purely a data-space edit — the mesh
   vertices change, the object transform does not yet.

4. **Translate the object** by the world-space delta the previous step
   introduced, so visually the part stays exactly where it was. After
   this step:
   - `obj.matrix_world.translation` lands on the part's world bbox center,
   - the part has not moved on screen,
   - rotating the part around its own origin spins it around its own
     center (which was the goal).

5. **Recompute the local bbox** now that the geometry is re-centered.
   Local bbox is symmetric around the origin (by construction); record
   `bbox_local_min`, `bbox_local_max`, `bbox_size`, diagonal, bounding
   sphere radius.

6. **Find the principal axis** via PCA on the (re-centered) vertices.
   This tells animation "the long direction of this part is `[x,y,z]`
   in local frame" — useful for rotating around the part's length, or
   for aligning a label to the part's spine.

7. **Find nearest neighbors** by part-to-part bbox-center distance,
   limited to N closest. Useful for "what does this part connect to?"
   and for the contract director's "show this part WITH its neighbors"
   beats.

8. **Write `<asset>/<part_id>.rest.json`** with the schema below. Also
   append to an `_index.json` and a top-level `_manifest.json`.

## Per-part JSON schema (`<part_id>.rest.json`)

```json
{
  "schema_version": "blender_director_v1",
  "part_id": "V8_Engine-281",
  "asset_id": "v8_engine",
  "role_hint": "piston_1A",
  "exported_at": "2026-05-27T00:42:00Z",

  "rest_transform": {
    "location_world": [-0.231, 0.142, -0.020],
    "rotation_euler_xyz_rad": [0, 0, 0],
    "rotation_quaternion_wxyz": [1, 0, 0, 0],
    "scale": [1, 1, 1],
    "matrix_world_rest_rowmajor": [16 floats]
  },

  "geometry_local": {
    "bbox_min": [-0.039, -0.029, -0.029],
    "bbox_max": [ 0.039,  0.029,  0.029],
    "bbox_center": [0, 0, 0],
    "bbox_size":   [0.078, 0.057, 0.057],
    "bbox_diagonal": 0.108,
    "bounding_sphere_radius": 0.055,
    "principal_axis": [1, 0, 0],
    "long_axis_length_m": 0.078,
    "vertex_count": 954,
    "face_count": 1812
  },

  "pivot_history": {
    "method": "bbox_center",
    "origin_world_before": [-0.231, 0.142, -0.020],
    "origin_world_after":  [-0.231, 0.142, -0.020],
    "shift_local_applied": [0.000, 0.025, 0.000]
  },

  "neighbors": [
    {"part_id": "V8_Engine-176", "distance_m": 0.024, "direction_world": [0.1, 0.9, 0.0]},
    {"part_id": "V8_Engine-287", "distance_m": 0.228, "direction_world": [0.0, 1.0, 0.0]}
  ]
}
```

`matrix_world_rest_rowmajor` is the single source of truth — a downstream
consumer that knows nothing else can place the centered mesh back into the
scene with one `mat4` multiply.

## Asset-level files

  - `<out_dir>/_index.json` — `{"parts": [{"part_id": "...", "json": "..."}]}`
  - `<out_dir>/_manifest.json` — aggregate (asset bbox, part count, units,
    notes, the exact Blender version + script timestamp).

## How to invoke

**Step 1 — verify your role-tagged parts are the RIGHT meshes.** This step
is mandatory before running `direct_blender` for the first time on a new
asset, or any time `manual_overrides.json` changes. It color-segments
every role-tagged part and renders 3 orthographic-ish views so a human
or vision model can confirm "yes, the parts I tagged `crank_throw_*`
really are the crankshaft, not the cam lobes."

```python
exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_blender_director.py").read())
verify_overrides(
    role_overrides_json=r"C:/SPATAIL_MAX/engineexplainer/engine/manual_overrides.json",
    out_dir=r"C:/tmp/v8_verify",
    name_prefix="V8 Engine",
)
# → C:/tmp/v8_verify/verify_front.png, verify_side.png, verify_3q.png
```

**The bug this prevents:** on the V8 we shipped a first cut of overrides
where `crank_throws` pointed at `V8_Engine-.162 / .160 / .158 / .156`.
Those parts are at Z=0.297 (TOP of the engine — actually cam lobes on
the cylinder heads). The real crankshaft throws live at Z=0.073 (BOTTOM
of the engine, in the valley of the V): `V8_Engine-.536 / .537 / .538
/ .539`. Animation against the wrong throws produced wrong-looking
motion that was hard to debug from the web side. Color-segmenting and
rendering would have caught it in 30 seconds. So we made it a step.

**Step 2 — center pivots + write rest JSONs.**

```python
direct_blender(
    asset_id="v8_engine",
    out_dir=r"C:/SPATAIL_MAX/assets_processed/rest_poses/v8_engine",
    # optional filters
    collection=None,          # or "Engine" to limit to a collection
    name_prefix=None,         # or "V8 Engine" to limit to those meshes
    role_overrides_json=r"C:/SPATAIL_MAX/engineexplainer/engine/manual_overrides.json",
    neighbor_k=4,             # how many nearest neighbors to record
)
```

Returns `{ asset_id, parts_processed, out_dir, manifest_path }`.

## The restore API

```python
restore_from_rest(rest_dir=r".../rest_poses/v8_engine")
```

Reads every `*.rest.json` in the directory and applies its
`matrix_world_rest_rowmajor` to the matching scene object. Use this
after experimentation has shifted parts around and you want to bring
everything back to its baked rest pose. Per-part — does not touch
parents, constraints, or modifiers.

## Why per-part files (not one big JSON)

The web runtime, the animator, the labeler, and the visual validator all
care about *one part at a time* most of the time. Per-part files let any
of them fetch only what they need, and let the director cheaply diff
"what changed for V8_Engine-281?" between two pipeline runs without
parsing a 600-part monolith. The `_index.json` and `_manifest.json` give
batch consumers the rollup view when they need it.

## Per-part rest data unlocks

  - **Modular animation.** "Rotate this part 45° around its local X axis"
    actually does what you'd expect. No more parts flying off into space.
  - **Reliable halos / labels.** The halo sprite goes at `location_world`,
    not at a bbox center that has to be recomputed each frame. Saves work
    in the web runtime and is consistent across pipeline rebuilds.
  - **Camera framing.** `frame_on(part)` becomes `frame_on(rest_transform.location_world, geometry_local.bbox_size)` — a one-step lookup.
  - **Procedural placement.** Move a part to a new location? Replace
    `location_world` in its rest JSON, leave geometry untouched, restore.
  - **Animation authoring.** The contract director can author motion as
    "from `rest_transform` move along `geometry_local.principal_axis` by
    N cm" — no scene inspection needed at director-time.

## Anti-goals

- ❌ Decide what kind of part this is (rod / piston / throw). That's the
  classifier's job. This skill is **geometry-only**.
- ❌ Build kinematic chains or constraints. That's the rigger.
- ❌ Pick a "semantic" pivot (rod's small end, throw's journal). That's
  what treat-mesh does. Blender-director picks the **uniform geometric
  center** intentionally so every part is interchangeable from a
  per-part-reasoning perspective.
- ❌ Add keyframes. Animation reads the rest data and stamps its own
  keyframes; this skill only stamps the rest.
- ❌ Touch materials, lights, cameras, or world settings.
- ❌ Mutate parent relationships. If a mesh has a parent, its
  `matrix_world` (which already composes the parent chain) is what gets
  recorded; the parent stays intact.

## Pipeline position

```
treat-mesh                  ← segments + initial origin-to-pivot
       ↓
classify-engine             ← per-part role labels
       ↓
blender-director  ★         ← uniform geometric-center pivots + rest JSONs
       ↓
rig-engine                  ← (optional) kinematic chains for mechanism animation
       ↓
animate-engine              ← reads rest JSONs + slot data, stamps keyframes
       ↓
export GLB                  ← downstream consumer (web runtime)
```

`blender-director` slots BETWEEN classify and rig. The rigger now has both
the semantic info (from classify) AND the modular rest poses (from this
skill); the animator no longer has to inspect the scene to figure out
where each part "should" sit.
