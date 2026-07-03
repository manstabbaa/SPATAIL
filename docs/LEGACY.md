# Legacy ledger (post tracked/placed pivot, 2026-06-10)

The pivot: SPATAIL = the XR layer. Two streams — object-TRACKED overlay (iOS 27 object
tracking) and world-PLACED (design-system placement). Meshy = asset artist; Blender =
scene constructor/animator only. See docs/apple-objecttracking/spatail-implications.md.

## Deleted in the 2026-07-03 teardown (recoverable from git history)

The new product client `ios/Spatail` + `ios/SpatailEngine` is committed and builds
green; all salvage porting from these lineages is done. Founder-approved deletions:

- `SPATAILMobileAR/` — the TRACKED-stream/Live-Vision prototype app; its perception,
  vision-uplink and contract code was ported into `ios/Spatail`.
- `ios/_legacy_Spatail/` — the pre-rebuild Spatail app; ModularContract/materials/step
  logic ported into `ios/Spatail`.
- `ios/SpatailViewer/` — the Engine Viewer scaffold (job-polling, progress UI ported).
- `ios/SpatailPlayer/` — the .spatail-bundle player lineage, plus its
  `tools/sync/*.mjs` codegen/sync guards (see `tools/sync/README.md`) and
  `pipeline/bundle_ios.js` / `pipeline/copy_contracts_to_ios.js`.
- `viewer/` — the old :5173 node web viewer (`viewer/server.js`); superseded by the
  WebXR viewer in `webxr/`.
- `engineexplainer/` (+ its intelligence server) — V8 spatial-contract research PoC,
  previously "parked in place"; the pipeline/blender sample-asset defaults that pointed
  at it were repointed/removed.
- `src/planner/` + `src/types/` (all of `src/`) — stale duplicates of the
  `pipeline/spatail` planners; nothing imported them.

Also retired then: the `git:sync`/`git:push` npm scripts (`tools/sync/git-*.sh`),
stale post-pivot.

## Deleted in the pivot cleanup (recoverable from git history)

> Note: the 2026-07 merge that unified the studio world with the fusion-brain world
> resurrected several of these trees; the 2026-07-03 teardown (above) re-deleted the
> ones that came back.
- `studio/educational/`, educational job mode in job_server/job_entry — retired book-wrapper path.
- `studio/mechanics/` — empty remnant of the deleted 41-mechanic catalog.
- `pipeline/server/` — old WebSocket session server, superseded by studio/server/job_server.py.
- `pipeline/spatail/` + `src/` + `viewer/` + `pipeline/*.js` + `demos/` + `scene_contracts/` +
  `spec/` — the legacy JS planner → web-viewer stack (pre-job-server).
- `SPATAILMobileAR/` + `ios/SpatailPlayer/` + `tools/sync/*.mjs` — pre-SpatailEducator
  iOS prototypes and their protocol-sync tooling.
- `skills/` — old skills catalog (procedural authoring era).
- `figma_tools/` — unwired placeholder.

## Legacy-in-place (parked, NOT on the live path — don't build on these)
- `studio/asset_factory/` (procedural, ≠ repo-root `asset_factory/`) — old Blender
  build-plan factory. Still imported by studio/representation/job_entry.py (opt-in
  representation mode). Goes when representation mode is replaced by the director path.
- `studio/library/catalog.py` + `builders.py` — procedural mechanics catalog. KEPT because
  `studio/meshy/meshy_normalize.py` reuses `library/bake_assets` export/fit utilities and
  `asset_library.py` is the live library registry. Only the catalog/builders are legacy.
- `pipeline/cad/` — CAD-from-manual path (manual-analyst/cad-modeler agent era).
- Most of `pipeline/blender/*.py` — procedural modeling/rigging drivers. LIVE exceptions:
  `spatail_live_blender.py` (the MCP live-Blender connector). The rest is the "Blender as
  asset generator" era; superseded by Meshy assets + Blender as scene constructor.
- `.claude/agents/` roles `manual-analyst`, `manual-segmenter`, `cad-modeler`,
  `cad-from-manual-lead`, `blender-integrator`, `senior-3d-artist` (as from-scratch
  modeler) — off-direction; superseded by the Meshy-first studio roles. The CAD-era
  roles also reference `engineexplainer/` paths deleted in the 2026-07-03 teardown,
  so they are inoperable as written.

## Live path (what the product actually runs)
PC: `studio/server/job_server.py` (+ blender_bridge/generator/llm_author/headless_build),
`studio/director/` (the brain: composer/experience/vision/logic/scene_contract),
`studio/spatail/` (design system/placement/room model), `studio/meshy/` (asset artist),
repo-root `asset_factory/` (ingest), `public/assets/spatail-library/` (served assets).
Phone: `ios/Spatail/` (+ `ios/SpatailEngine/`). Cowork: `cowork-plugin/` (asset ops).
