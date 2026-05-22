---
name: spatail-classify-engine
description: Read a treatment manifest from an internal-combustion engine CAD and assign semantic roles (crank_throw / piston / connecting_rod / wrist_pin) to each part, plus pair them into kinematic groups (cylinders). Engine-specific consumer of the generic mesh treatment.
when_to_use: After spatail-treat-mesh has run on a piston-engine CAD (V-engine, inline, boxer) and downstream rigging/animation needs to know which part is which.
---

# Engine Part Classifier

## What this exists for

`spatail-treat-mesh` produces a `treatment.json` that knows nothing about engines — it knows shapes (rod-like / disc-like / blob) and pivots. Something has to translate that geometric study into engine-specific roles before any rig step can attach a Damped Track to "the rod for cylinder 3 bank A."

This skill is that translator. Specifically, for a piston engine, it:
- Identifies the **crank throws** (blob-class parts arranged along a single line).
- Identifies the **pistons** (disc-class parts clustered into two banks).
- Identifies the **conrods** (longest rod-class parts, roughly = stroke + (rod-length)).
- Identifies the **wrist pins** (shortest rod-class parts).
- Pairs them into **kinematic groups** (one per cylinder): (piston, rod, pin, throw, bank).

Output: `<asset>.classification.json`. Downstream rigging consumes this; never touches mesh.

## Input

- `<out_dir>/<asset>.treatment.json` from `spatail-treat-mesh`.
- Each part has: name, vertex_centroid, bbox_centre, principal_axis, eigvals, shape_class, pivot_world.

## Strategy

### Step 1: find the crank axis
- Filter to `shape_class == "blob"` AND verify centroids cluster along a single line (small spread perpendicular to the line, large spread along it).
- Fit a line: take the centroids, do PCA on them; primary axis = crank axis direction; line passes through their mean.
- The 5 (or however many) blobs whose centroids lie tightly along this line are the **crank throws**.
- Sort throws by their projection along the crank axis → cylinder index 1..N.

### Step 2: identify pistons
- Filter to `shape_class == "disc-like"`.
- For each disc, check that its principal_axis is roughly **perpendicular** to the crank axis (a piston's bore axis is perp to the crank).
- Cluster pistons by their projection along the crank axis → that's the piston's cyl index.
- Within each cyl, split into bank A / bank B by sign of (piston_centre − throw_centre) projected onto a "bank axis" perpendicular to crank.

### Step 3: identify rod-class parts (rods vs pins)
- Filter to `shape_class == "rod-like"`.
- Bimodal split by length:
  - **Conrods**: longer rod-likes (length ≈ stroke × rod-to-stroke-ratio, typically 14-20cm at engine scale).
  - **Wrist pins**: shorter rod-likes (≈ piston bore diameter, typically 4-8cm).
- A k-means with k=2 on lengths usually splits these cleanly.

### Step 4: pair into cylinders
For each (piston, throw) pair found in step 2:
- Find the conrod whose endpoints best match (one near piston centroid, other near throw journal).
- Find the wrist pin nearest the piston centroid.
- Assign cyl + bank to all four parts.

### Step 5: handle missing parts gracefully
- If a wrist pin is missing for some cyl (the V10 has 9 pins for 10 cyls in this OBJ), flag it and continue.
- If counts don't match (e.g. only 4 throws found), emit a warning, don't crash.

## Output schema

```json
{
  "assetId": "v10_engine",
  "schemaVersion": "0.1.0-spatail-classify-engine",
  "classifiedAt": "ISO-8601",
  "crank": {
    "axis_direction": [0, 0, 1],
    "axis_origin": [8.5, -17.7, 0]
  },
  "parts": [
    {"name": "V12 Engine Assembly.001", "role": "crank_throw",
     "cylinderIndex": 1, "throwId": "throw_1", "journalCentre": [...]},
    {"name": "V12 Engine Assembly.005", "role": "piston",
     "cylinderIndex": 1, "bank": "A"},
    {"name": "V12 Engine Assembly.012", "role": "connecting_rod",
     "cylinderIndex": 1, "bank": "A", "length_cm": 17.0},
    {"name": "V12 Engine Assembly.022", "role": "wrist_pin",
     "cylinderIndex": 1, "bank": "A", "length_cm": 5.7}
  ],
  "kinematicGroups": [
    {"id": "cyl_1_A", "cylinderIndex": 1, "bank": "A",
     "piston": "...", "rod": "...", "pin": "...", "throw": "..."}
  ],
  "summary": {
    "throwCount": 5,
    "pistonCount": 10,
    "rodCount": 10,
    "pinCount": 9,
    "cylinderGroupsComplete": 9,
    "cylinderGroupsMissingPin": 1
  }
}
```

## Anti-goals

- ❌ Re-inspect mesh geometry. Treatment already did that. If a fact is missing from the manifest, extend `treat_mesh`, don't sneak inspection in here.
- ❌ Decide which way the engine "should" be oriented (V-angle convention, firing order). That's a downstream mechanism skill.
- ❌ Move objects, set origins, change parents. The manifest is read-only input here; we produce only JSON.

## How to invoke

```python
exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_classify_engine.py").read())
classification = classify_engine(
    treatment_json=r".../v10_engine.treatment.json",
    out_json=r".../v10_engine.classification.json",
)
```
