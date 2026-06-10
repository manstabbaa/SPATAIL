# Legacy ledger (post tracked/placed pivot, 2026-06-10)

The pivot: SPATAIL = the XR layer. Two streams — object-TRACKED overlay (iOS 27 object
tracking) and world-PLACED (design-system placement). Meshy = asset artist; Blender =
scene constructor/animator only. See docs/apple-objecttracking/spatail-implications.md.

## Deleted in the pivot cleanup (recoverable from git history)
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
- `engineexplainer/` — V8 spatial-contract research PoC. Parked in place because
  pipeline/blender scripts reference its sample assets and it carries an uncommitted
  foreign edit. The contract patterns inspired the current direction; the code is not live.
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
  `cad-from-manual-lead`, `senior-3d-artist` (as from-scratch modeler) — off-direction;
  superseded by the Meshy-first studio roles.

## Live path (what the product actually runs)
PC: `studio/server/job_server.py` (+ blender_bridge/generator/llm_author/headless_build),
`studio/director/` (the brain: composer/experience/vision/logic/scene_contract),
`studio/spatail/` (design system/placement/room model), `studio/meshy/` (asset artist),
repo-root `asset_factory/` (ingest), `public/assets/spatail-library/` (served assets).
Phone: `ios/SpatailEducator/`. Cowork: `cowork-plugin/` (asset ops).
