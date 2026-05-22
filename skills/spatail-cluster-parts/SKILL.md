---
name: spatail-cluster-parts
description: Spatial clustering for high-detail CADs. Takes a treatment.json with thousands of mesh islands (each screw, wire, button-cap separate) and groups them into a manageable number of functional clusters (~30-80) by 3D proximity. Outputs cluster assignments + per-cluster summary JSON. Unblocks downstream classification, labelling, and visual layer work on real-world detailed CADs that ship with extreme topology.
when_to_use: When treat-mesh produces more parts than can be meaningfully labelled (rule of thumb: >100 parts). Run BEFORE classify-* so the classifier sees clusters as "parts" instead of every screw individually. The Mercedes dashboard case (4,972 parts) is the canonical example.
---

# Cluster-Parts Skill

## What it solves

CAD models authored at production fidelity ship with thousands of mesh islands. A real example: the Mercedes dashboard OBJ has 4,972 separate parts — every individual screw, wire bundle, button cap, indicator LED is its own mesh. **No human ever wants 4,972 labels.** They want ~30 functional groups: "the climate control cluster," "the steering wheel buttons," "the gear selector," etc.

This skill bridges raw topology to functional groups.

## Algorithm

**DBSCAN on bbox centroids** with adaptive epsilon:

1. Read all part bbox centroids from `treatment.json`
2. Compute the asset's bbox diagonal
3. Set `eps = diagonal × 0.02` (parts within 2% of asset size cluster together — typical button-assembly scale)
4. Set `min_samples = 2` (any 2+ nearby parts form a cluster)
5. Run DBSCAN; parts with no nearby neighbours become single-part clusters
6. For each cluster, compute:
   - bbox (union of member bboxes)
   - bbox centroid
   - shape mix (histogram of member shape classes)
   - member count
   - representative axis (if mostly elongated)
   - candidate role hint (e.g., "button assembly" if mostly small disc-likes near a planar surface)

## Output

`<asset>/<asset>.clusters.json`:

```json
{
  "assetId": "f1_steering_wheel",
  "schemaVersion": "0.1.0-cluster-parts",
  "params": {"eps_cm": 4.5, "min_samples": 2},
  "summary": {
    "input_parts": 4972,
    "output_clusters": 47,
    "single_part_clusters": 8,
    "largest_cluster_size": 412
  },
  "clusters": [
    {
      "cluster_id": 0,
      "member_part_ids": ["Steering Wheel.123", "Steering Wheel.124", ...],
      "bbox_lo": [...], "bbox_hi": [...],
      "centroid_world": [...],
      "shape_mix": {"rod-like": 6, "disc-like": 2, "irregular": 1},
      "candidate_hint": "button_assembly"
    },
    ...
  ]
}
```

## How it composes with the rest of the toolset

```
treat-mesh        → 4972 mesh islands
 ↓
cluster-parts     → 47 functional clusters    ← NEW
 ↓
classify-*        → labels each CLUSTER (not each part) with a role
 ↓
measure-per-       → measurements on the cluster's union geometry
 part
 ↓
reassemble + rig  → uses cluster-level pivots
 ↓
animate + render
```

Downstream skills change one line: they now iterate clusters instead of parts.

## Anti-goals

- ❌ Replace classification. This is *grouping*, not naming.
- ❌ Make assumptions about asset type. Pure spatial proximity, asset-agnostic.
- ❌ Touch mesh data. Read-only on treatment.json + scene.
