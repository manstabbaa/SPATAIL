---
name: spatail-merge-intelligence
description: Intelligence-driven mesh merge for welded CADs where one logical part is exported as many separately-named sub-meshes that share boundaries. Two passes — vertex-touching at ~50 microns catches halves of a button cut in two, then concentric-axis via PCA catches stacked layers of a rotary knob with a tiny gap between them. Outputs joined `part_NNNNN` Blender objects.
when_to_use: Run AFTER spatail-treat-mesh on assets where CAD authoring left a single physical part as multiple named meshes welded along shared vertex boundaries (F1 steering wheel, automotive interior dashboards, any modeled-in-pieces hard-surface asset). Replaces spatail-cluster-parts when the CAD is high-detail enough that spatial proximity alone over-merges. Skip for simple assets with one mesh per logical part.
---

# Merge-Intelligence Skill

## What it solves

Production CAD often exports one physical part as several welded sub-meshes — the two halves of a steering-wheel paddle are separate objects that share their seam vertices, the cap and the toothed ring of a rotary knob sit stacked on the same axis with a 1 mm air gap. Treating them as distinct logical parts produces nonsense rigs and unlabellable scenes.

Centroid-proximity clustering (spatail-cluster-parts) over-merges on these CADs because everything is close to everything. The smarter "same physical part" signal is **shared boundary**: two halves of one button share a vertex line. Two physically separate buttons sitting 5 mm apart do NOT share vertices. That's the discriminator this skill exploits.

For the stacked-layer case (concentric knob rings with a tiny gap), no shared verts exist — so a second pass uses PCA principal axes and checks whether two components are co-linear and overlap along their shared axis.

## Algorithm

**Pass 1 — Vertex-touching (union-find)**

1. Collect all candidate mesh objects.
2. Bucket by coarse bbox grid → only test pairs whose bboxes overlap.
3. For each candidate pair, hash A's world vertices into ε-sized cells; for each B vertex, probe the 3³ neighbouring cells for a vertex within ε.
4. Count `shared` (number of B verts that found an A neighbour) and the fraction `shared / smaller_mesh_vertex_count`.
5. Merge the pair iff `shared ≥ min_shared_verts` AND `frac ≥ min_shared_frac`. The fraction test is critical — it prevents transitive closure across the whole asset from incidental T-junction touches.

**Pass 2 — Concentric-axis (PCA)**

1. For each multi-vertex component, compute the centroid and dominant PCA eigenvector by power iteration. Record the axis-span (min/max projection along the axis).
2. Bucket components by bbox; for each candidate pair:
   - axes must align: `|axis_a · axis_b| ≥ angle_dot_min`
   - the smaller's centroid must lie within `radial_eps_cm` of the larger's axis line (perpendicular distance test)
   - their projections along the shared axis must overlap or be within `axis_overlap_slack`
3. Union-find merges satisfying triples.

**Pass 3 — Join**

For each multi-member component: select all members, set the largest-volume one active, `bpy.ops.object.join()`, rename to `part_{root_idx:05d}`.

## Tuning knobs

The function defaults are tuned for tightly-welded high-detail CADs (F1 steering wheel class):

| Param | Default | Meaning |
|---|---|---|
| `eps_cm` | `0.005` | Vertex-touching tolerance — 50 microns |
| `min_shared_verts` | `2` | Pair must share ≥ this many verts |
| `min_shared_frac` | `0.05` | Shared count must be ≥ 5% of the smaller mesh |
| `radial_eps_cm` | `0.8` | Concentric-axis radial tolerance (8 mm) |
| `angle_dot_min` | `0.88` | Concentric axes must align to this dot product |
| `axis_overlap_slack` | `3.0` | Max along-axis gap between concentric components (cm) |

**For looser mechanical assemblies** (engines, suspension, anything where parts genuinely sit close without being welded), tighten:

- `min_shared_verts = 6`
- `min_shared_frac = 0.20`

so that incidental contact between distinct parts doesn't trigger a merge.

## Empirical result

F1 steering wheel CAD with `eps_cm=0.005, min_shared_verts=2, min_shared_frac=0.05`:

- input objects: **4972**
- after vertex-touching pass: a few hundred components
- after concentric-axis pass: **79 logical parts**

That's ~63x compression to a labellable scene with no human intervention.

## Output

In-place rename of joined Blender objects to `part_00000`, `part_00001`, … (the root_idx is the union-find root index, so it's stable for a given input ordering). The returned dict reports:

```python
{
  "input_objects": 4972,
  "after_touching": 412,
  "concentric_merges": 38,
  "after_concentric": 374,
  "joined_clusters": 79,
  "joined_parts": 4972,
  "final_object_count": 79,
}
```

## How to invoke

```python
exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_merge_touching.py").read())
merge_touching_in_scene(
    eps_cm=0.005,
    min_shared_verts=2,
    min_shared_frac=0.05,
    do_concentric=True,
    radial_eps_cm=0.8,
)
```

## How it composes

```
treat-mesh           → 4972 named mesh objects with clean origins
 ↓
merge-intelligence   → 79 welded part_NNNNN objects        ← THIS SKILL
 ↓
classify-*           → labels each part_NNNNN with a role
 ↓
measure / rig / animate / render
```

For **high-detail welded CADs**, this skill replaces `spatail-cluster-parts` in the pipeline — vertex-touching is a much sharper signal than centroid proximity on this class of asset. For simple CADs where treat-mesh already gives clean one-mesh-per-part output, skip both.

## Anti-goals

- Not classification — does not assign names or roles. Pure geometric grouping.
- Not semantic — knows nothing about steering wheels, engines, or knobs. Vertex sharing and axis colinearity are the only signals.
- Asset-agnostic — same code path for any welded hard-surface CAD.
- Not a topology fixer — does not weld vertices, does not remesh, does not change geometry. Only joins objects.
