# Blender as the spatial intelligence — plan

> 2026-06-12. Product driver: on-device testing showed the spatial-UI anchors land on
> guessed bbox points, not on the mesh ("the frog's eye"). The fix is architectural:
> Blender has the real mesh in 3D — it computes anchors, segments parts, and builds
> the scene; the phone plays what's baked. This doc is the implementation plan,
> grounded in the current spine (file refs throughout). PC implements the studio/
> side; the iOS side is either already shipped or called out explicitly.

## Ground truth (recon 2026-06-12)

- The live Meshy chain (`studio/meshy/asset_service.py`) is: Gemini 4-view PNGs
  (KEPT at `studio/out/meshy/<slug>/view_*.png`) → Meshy 30k tris → Blender
  normalize → decimate (12k tris/1024px) → animate (whole-object `anim_root`,
  named clips) → native `bpy.ops.wm.usd_export`.
- **Structure is destroyed on purpose today**: normalize JOINS all meshes into one
  object (`meshy_normalize.py:56`), decimate joins again (`decimate.py:67`), and the
  manifest hardcodes `"parts": []` (`asset_service.py:269-273`).
- Object names DO survive to USDZ as prims (proven by the procedural path) — so
  named parts, once they exist in Blender, reach RealityKit findable-by-name.
- iOS already decodes and renders `step.target` (part name) and `step.anchorOffset`
  (normalized bbox point) with callout + leader line + pulsing marker + material
  highlight (`ModularContract.swift` Step, `ModularRuntime.resolveStepAnchor`).
  **No studio code emits either field yet.**
- No ray_cast/BVH/back-projection code exists anywhere; no ML deps (no numpy/torch/
  SAM); Gemini calls today return text/images only — but the parked code has
  multi-view render rigs (`pipeline/blender/spatail_multiview_render.py`,
  `segment_and_analyze.py`) and region transport prior art
  (`spatail_mesh_region.py`: vertex groups don't survive USD/glTF → ship a sidecar
  JSON or a baked overlay mesh).
- RoomModel (real table w/d/h from ARKit) exists on BOTH sides but never crosses:
  POST /modular accepts no room payload (`job_server.py:417-527`), and
  `studio/spatail/placement_solver.solve_serialized` is dead code.
- One `generationJobId` per contract, primary asset only; no per-step asset
  introduction; manifest filename mismatch (`{job_id}_manifest.json` written,
  `{asset_id}_manifest.json` loaded by `experience.py:48`); iOS drops
  `manifest_url` when polling jobs (`GenerativeClient.swift:25`).

## Phase 1 — Blender-computed anchors (kills the on-device guessing)

New ANCHORS stage in the Meshy chain, after normalize (mesh at final
metres/orientation), before decimate:

1. Blender renders K canonical views of the normalized mesh (port the parked
   multiview rig; intrinsics are exact because we own the cameras).
2. Gemini gets the renders + the asset subject and returns **2D points per semantic
   feature** ("eye", "mouth", "front legs") per view. (New prompt; the Gemini API
   supports pointing — today's calls just never ask for coordinates.)
3. Back-project in Blender: ray from each camera through the 2D point onto the mesh
   (`BVHTree`/`scene.ray_cast` — new code, ~50 lines), cluster the per-view hits →
   canonical surface anchors `{name, pos_m (asset-local Y-up), normal, confidence}`.
4. QA loop: re-render with marker dots composited, ask Gemini "is the dot on the
   eye?" — retry once on no.
5. Write anchors into the manifest (extend `spatail-asset-manifest/2` → `/3`:
   `anchors: [...]`), and have the director map step keywords → anchors → emit
   `step.target` + `step.anchorOffset` ((pos − bbox.min)/extents). iOS is done.

Why first: no segmentation needed, no new ML deps, lands directly on the shipped
iOS anchor ladder, and fixes the user-visible pain today.

**Cross-cutting fixes that ride with Phase 1** (all blockers found in recon):
- manifest filename: write `{asset_id}_manifest.json` (or load by job id) so
  `experience._load_manifest` actually finds Meshy manifests.
- `composer._validate_sequence` whitelist: keep `target`/`anchorOffset` instead of
  stripping unknown step keys; extend the sequence-authoring Gemini prompt to
  reference anchors by name per step.
- iOS: decode `manifest_url` from job polling and request it (small).

## Phase 2 — mesh segmentation (the "CAD structure" goal)

A Meshy mesh is one watertight textured blob — loose-parts separation finds ~1
island. The ladder:

- **2a. Stop destroying structure** (prereq, cheap): preserve whatever separate
  objects Meshy delivers; join only as a fallback. Touches `meshy_normalize.py:56`,
  `decimate.py:67`, and animate's single-`anim_root` assumption (parent the parts
  under it instead).
- **2b. Multi-view semantic labels → per-face labels → split**: same render loop as
  Phase 1 but Gemini returns per-part regions (polygons/boxes per view). In bpy:
  for each face, project its centroid into each view, check visibility (BVH ray,
  occlusion), accumulate label votes → per-face labels → `bmesh` split by label →
  **named objects** (`frog_eye`, `frog_body`) that survive to USDZ prims. Pure
  bpy/mathutils — no torch. Optional later upgrade: SAM on the PC GPU for crisper
  masks (new dependency, opt-in only if Gemini polygons prove too coarse).
- **2c. Two highlight transports** (lesson from `spatail_mesh_region.py`):
  - HARD parts (brake-duct chunks): real submeshes → iOS material-swap highlight
    and explode work as-is.
  - SOFT regions (frog's eye — splitting would scar texture/silhouette): bake an
    **overlay highlight mesh** (extracted faces, offset along normals, emissive
    semi-transparent blue — the "misty focus" look) named `spatail_region__eye`,
    shipped inside the USDZ, hidden by default. iOS toggles its visibility per step
    (small runtime addition; today's material-swap stays as fallback).
- **2d. Master material**: lives in Blender as one shared node group (Meshy PBR +
  highlight mix input) for authoring. Constraint to respect: USDZ→RealityKit bakes
  to UsdPreviewSurface — arbitrary shader graphs do NOT survive. What ships is
  normal PBR + overlay meshes / baked variants. Don't fight this on-device.
- **2e. Exploded views**: with named parts, reuse the parked
  `measure_exploded_view` logic (per-part radial offsets, z-stratum ordering) and
  bake `explode`/`collapse` as **named clips** — the runtime already plays named
  clips, so exploding a brake duct costs zero new iOS code. Write real `parts[]`
  into the manifest.

## Phase 3 — scene creation tied to the real room (terrain stage)

- **Room plumb**: phone includes a compact serialized RoomModel snapshot (best
  table w/d/h, floor height, bounds) in the POST /modular body (iOS small);
  `job_server` parses it and threads it into the build spec. Server-side
  `placement_solver.solve_serialized` finally gets wired for validation.
- **Stage builder**: new headless entry that imports the normalized hero GLB and
  composes around it (today the Meshy lane and scene-construction lane are
  disjoint). v1 stage = **circular terrain cutout sized to the scanned table**
  (diameter = min(table_w, table_d) × 0.65 coverage): displaced mud disc, water
  inset, grass ring — bmesh + displace, house style per `studio/blender/realworld.py`.
  Hero seated on the stage. Export the stage as its own asset so the hero stays
  reusable in the library; the stage is per-room.
- Phone already places the scene at the table centre with the design-system solver;
  scale note ("Scaled to your table") per §5.
- Style-pass exclusion: the clay style/introspect pass operates on `gen_root` only —
  composed Meshy content keeps its PBR look (new exclusion rule).

## Phase 4 — scene evolution (frog enters alone; the world grows around it)

- **Contract**: per-step asset introduction — `asset.appearAtStep` (or
  `step.introduce: [assetId]`); tolerant decode on iOS + show/hide holders per step
  (small runtime change, ghosting already exists).
- **Server**: multi-asset generation queue — replace the single `generationJobId`
  with a list of `{assetId, jobId}`; iOS `streamGenerated` loops them; props stream
  in when ready, placeholders until then (already supported).
- **Blender-first prop rule**: props the constructor can build procedurally (lily
  pad: curve + bmesh + materials; pond disc; rocks) are built in Blender — Meshy is
  reserved for fidelity-critical props. Either way props register in the library
  and compound (`prop`/`environment` categories).
- Steps focus props (`step.focus` = any asset id — already works end-to-end,
  including the new anchored callouts).

## Order & ownership

1. **P1 anchors + contract fixes** — small, highest leverage, fixes today's pain.
2. **P2c soft-region overlays (eye highlight)** via 2b's label loop — the immersive
   "focus point" the product needs; hard splits + explode (2e) follow.
3. **P3 terrain stage** — independent of segmentation, high wow, needs the room plumb.
4. **P4 evolution** — mostly contract + queue mechanics.

PC owns: everything under studio/ (all four phases' spine work).
Mac/iOS owns (mostly shipped): anchor ladder ✅, callout/highlight UI ✅,
`manifest_url` consumption, RoomModel upload, overlay-visibility toggle,
`appearAtStep`, multi-job streaming loop.
