---
name: spatail-mesh-select
description: Strong sub-mesh selection for XR authoring — a mesh isolator/finder that ranks scene objects against a fuzzy query, plus a per-vertex/face/edge finder that resolves a composable region spec (or an everyday phrase like "top rim", "outer shell", "south face") into exact index sets in the mesh's own frame. Bakes selections into transportable overlay meshes + a regions.json sidecar, and highlights them at runtime via the highlight_region contract action. Use whenever a walkthrough must point at a part OF a part — a vein, a seam, a face patch, a rim — not just a whole mesh.
when_to_use: When a step needs to highlight a region smaller than one mesh (the existing whole-mesh highlight only addresses meshN objects). Run select → bake/emit in Blender during authoring; the runtime consumes the sidecar. glTF/USD cannot carry vertex groups, so the overlay-mesh bake is the authoritative transport.
---

# Sub-Mesh Selection Skill

The rest of the pipeline addresses geometry at the whole-mesh (`meshN`) level.
This skill adds the missing primitive: **point at a region inside one mesh** and
carry that region all the way to the runtime highlight.

Two Blender modules + one runtime contract action:

- `pipeline/blender/spatail_mesh_select.py` — find + select (read-only)
- `pipeline/blender/spatail_mesh_region.py` — bake + emit (transport)
- runtime `highlight_region` action (web `viewer.js` / `contract_player.js`)

## 1. Mesh isolator / finder

```python
import sys; sys.path.insert(0, r"C:/SPATAIL_MAX/pipeline/blender")
import importlib, spatail_mesh_select as ms; importlib.reload(ms)

ms.find_mesh("lantern")                 # → ranked candidates, best first
ms.find_mesh("arm", spatial={"side": "left"})
ms.find_mesh("hub", registry=fan_registry_dict)   # role/alias-aware
```

Returns `[{name, score, reasons[], centroid_world, bbox_world, dims, n_verts}]`.
Scoring: exact name > name substring > shared tokens > datablock match >
registry role/alias, plus an optional spatial bias (`side` =
left/right/front/back/top/bottom, or `near_world`). **Always pick from the
ranked list — never assume a name.**

## 2. Per-vertex / face / edge finder

`select_region(mesh_name, spec)` resolves a spec to exact index sets. Selection
runs in **normalized local-bbox coordinates (0..1 per axis)** so the same spec
works on a 6 cm fan and a 6 m lighthouse. World centroid/bbox/radius are also
returned.

### Region spec predicates

Vertex predicates (normalized unless noted):

| Predicate | Meaning |
|---|---|
| `axis_band {axis, lo, hi}` | keep verts whose norm coord on axis ∈ [lo,hi] |
| `half_space {axis, side:'+'/'-', at}` | one side of a plane |
| `box {lo_norm:[x,y,z], hi_norm:[x,y,z]}` | norm bounding box |
| `sphere {center_world|center_local, radius}` | world-unit ball |
| `radial_band {axis, r_lo, r_hi}` | distance from the axis, normalized by max radius |
| `near_vertex {index, radius}` | world-radius around a seed vert |
| `linked_from {index} \| {near_world}` | connected component (flood fill) |
| `combine: 'all' \| 'any'` | AND (default) / OR the vertex predicates |

Face predicates: `normal_dir {dir:[x,y,z], min_dot}` (world normal alignment),
`material "Name"`. Edge predicates: `sharp_edges {angle_deg}`, `boundary: true`.

### Composition — use a pipeline for relative terms

Relative predicates (`radial_band` "rim/outer/core") normalize against the
**current survivors**, not the whole mesh. Chain stages so "rim" means the outer
ring *of the band already selected*:

```python
ms.select_pipeline("LH_Tower", [
    {"box": {"lo_norm":[0,0,0.78], "hi_norm":[1,1,1]}},   # the top band
    {"radial_band": {"axis":"z", "r_lo":0.8, "r_hi":1.0}},# outer ring of THAT band
])
```

This matters: a tapered tower's narrow top deck sits at a smaller absolute
radius than the body — a single global radial filter would miss it.

### Everyday phrases

`region_from_phrase(mesh, phrase)` maps words → a pipeline:
positional bands (`top/bottom/upper/lower/left/right/front/back/middle/tip/
base/crown/cap`) applied first, then a shell word (`rim/edge/lip/ring/seam` →
outer ring; `outer/shell/skin/surface` → outer shell; `core/inner/center/axis`
→ inner core) applied within the band.

```python
ms.region_from_phrase("LH_Tower", "top rim")     # outer ring of the top
ms.region_from_phrase("LH_Lantern", "outer")     # glass shell
```

### Live preview (optional)

`ms.select_in_viewport(mesh, sel, kind="VERT"|"FACE"|"EDGE")` makes the
selection live in Blender's edit mode for visual confirmation.

## 3. Transport — sidecar + overlay bake

glTF and USD **cannot carry vertex groups**, so a selection must become geometry
or data the exporter and runtime can see.

```python
import spatail_mesh_region as mr; importlib.reload(mr)

sel = ms.region_from_phrase("LH_Tower", "south face")
sel["id"] = "tower_south"; sel["label"] = "Tower south face"

mr.bake_region_overlay("LH_Tower", "tower_south", sel)   # → overlay mesh in GLB
mr.emit_region_sidecar(r".../regions.json", [sel], asset_id="lighthouse")
```

- **Overlay mesh bake (authoritative).** Extracts the selected faces into a
  separate thin, normal-offset mesh `spatail_region__<mesh>__<id>` with an
  emissive highlight material, hidden by default. Because it lives *inside the
  GLB*, it is automatically in the runtime's coordinate frame — no axis/scale
  reconciliation needed. A sub-region becomes a whole-mesh in transport, so the
  existing whole-mesh runtime highlights it with no new GPU path. Needs faces;
  vert/edge-only regions use the sidecar halo instead.
- **regions.json sidecar (always).** Per region: `id, label, meshId, counts,
  centroidWorld, bboxWorld, radiusWorld, overlayMesh, indices{vertices,faces,
  edges}`. Lets a contract reference a region by id and gives the runtime a halo
  fallback. The world coords are **advisory** (the GLB is re-oriented Y-up);
  the overlay mesh is the precise path.
- `mr.clear_region_overlays(mesh_name=None)` removes baked overlays.

## 4. Runtime — `highlight_region`

The web bundle declares `regionsUrl` on the asset; `main.js` fetches it and
calls `viewer.setRegions(payload)` (overlay meshes are forced hidden on load).

Contract action:

```json
{ "type": "highlight_region", "region": "tower_south", "color": "#5046E5", "intensity": 1.4 }
```

`viewer.highlightRegion(id)` resolves in order: (1) baked overlay mesh — show +
emissive + halo, in-frame; (2) parent `meshId` — coarse but in-frame; (3) a halo
at the advisory `centroidWorld`. `resetHighlights()` re-hides overlays and clears
halos, so each beat is a clean slate.

## Invocation summary

```python
import sys; sys.path.insert(0, r"C:/SPATAIL_MAX/pipeline/blender")
import importlib, spatail_mesh_select as ms, spatail_mesh_region as mr
importlib.reload(ms); importlib.reload(mr)

m   = ms.find_mesh("the part you mean")[0]["name"]
sel = ms.region_from_phrase(m, "top rim")          # or select_region / select_pipeline
sel["id"] = "my_region"; sel["label"] = "Human label"
mr.bake_region_overlay(m, "my_region", sel)
mr.emit_region_sidecar(r"<bundle>/regions.json", [sel], asset_id="<asset>")
```

## Anti-goals

- ❌ Assume mesh names — always resolve via `find_mesh`.
- ❌ Rely on the sidecar's world coords for runtime placement — bake the overlay; it travels in the GLB frame.
- ❌ Carry selections as vertex groups through glTF/USD — they do not survive; bake or emit.
- ❌ Touch materials/cameras of the source mesh — overlays are separate objects.
- ❌ Use a single global `radial_band` for "rim of the top" — chain a pipeline so the shell normalizes within the band.
