# SPATAIL — Spatial Experience Engine

This document maps the SPATAIL one-pager onto the code that implements it,
and explains where to extend each layer.

## What the one-pager said, and where it lives

> "SPATAIL is a spatial experience engine. Given a prompt, it decides
> what should appear, where it should live in the physical environment,
> how large it should be, how users should interact with it, and how
> attention should be guided over time."

The five decisions map directly onto code:

| Decision                                | Where it's made                                  |
| --------------------------------------- | ------------------------------------------------ |
| What should appear                      | `pipeline/spatail/understanding.js` — per-source content typing |
| Which representation each piece takes   | `pipeline/spatail/representation_selector.js` (public: `src/planner/RepresentationSelector.js`) |
| Where it lives in the physical room     | `pipeline/spatail/placement_engine.js` (public: `src/planner/SpatialPlacementEngine.js`) |
| How large / what scale                  | same module (`scaleMode` per element)            |
| How users interact                      | `experience_reasoning.js#interactionsFor` (public: `src/planner/ExperienceReasoning.js`) |
| How attention is guided over time       | `experience_reasoning.js#buildAttentionPlan`     |

The runtime JSON schema lives in `schemas/spatialExperienceContract.schema.json`
and JSDoc typedefs in `src/types/SpatialExperienceContract.js` — both stay in
lockstep with `pipeline/spatail/experience_contract.js`.

The output is one **`SpatialExperienceContract.json`** per experience. The
contract is consumed by the web viewer today and (per the existing
[visionos_export/README.md](../visionos_export/README.md)) by the visionOS
player next.

## The five-layer pipeline

```
        Card JSON  (demos/*-card.json)
             │
             ▼
   ContentIngestionLayer        pipeline/spatail/content_ingestion.js
        normalize sources, probe /assets_raw, resolve groups
             │
             ▼
  SpatialUnderstandingLayer     pipeline/spatail/understanding.js
        detect domain, classify each source into a contentType,
        bucket facts, build target/relationship graph
             │
             ▼
   RepresentationSelector       pipeline/spatail/representation_selector.js
        per element: 2D panel? wall dashboard? exploded view?
        floor timeline? floating decision card? — with a reason
             │
             ▼
   SpatialPlacementEngine       pipeline/spatail/placement_engine.js
        per element: wall / table / floor / object_anchored /
        above_target / near_user — with a position in metres
             │
             ▼
   SpatialExperiencePlanner     pipeline/spatail/experience_planner.js
        assigns ids, sequences attention plan, resolves anchors,
        emits the final contract
             │
             ▼
   SpatialExperienceContract    pipeline/spatail/experience_contract.js
        schema, vocabularies, contract + element builders
             │
             ▼
        scene_contracts/*-spatial-contract.json
             │
             ▼
              ┌────────────────────────┬─────────────────────────┐
              ▼                        ▼                         ▼
         Web viewer            visionOS player              Future runtimes
   viewer/spatail.{html,js}    (v0.2, see visionos_export/README.md)
```

## The product principle in code

> "Do not force everything into 3D. SPATAIL must decide the best spatial
> representation for each piece of content."

This rule lives in `representation_selector.js`. Every `contentType` has
an explicit `representationMode` mapping, and the *reason* string ships
with the element so the viewer can show it:

- Numbers, status, summaries, lists, insurance, history → `two_d_panel` / `wall_dashboard`
- Physical parts → `highlighted_target` (bright translucent shader)
- Inspectable mechanisms / systems → `tabletop_model` / `three_d_model`
- Assemblies that need to be understood part-by-part → `exploded_view`
- Sequences over time → `floor_timeline`
- Physical interaction points (clips, screws, ports) → `anchored_callout`
- Diagnoses explaining *why* a task exists → `diagnostic_overlay`
- Visual derivative connecting two elements → `guide_line`
- Decisions / next-actions → `floating_decision_card`
- Service / repair steps → `two_d_panel` on the user's working side (right by default)

Every `spatialElement` carries `whyThisRepresentation`, `whyThisPlacement`,
and `attentionBehavior` (`active_focus` / `persistent_context` / `guiding`
/ `ambient` / `on_demand` / `peripheral`). `whyThisRepresentation` and
`whyThisPlacement` are required — `buildSpatialElement` throws if either is
missing. Every choice is explainable.

## The closed vocabularies (v0.2)

```
representationMode  two_d_panel · wall_dashboard · three_d_model ·
                    tabletop_model · floor_timeline · floating_decision_card ·
                    highlighted_target · exploded_view · anchored_callout ·
                    guide_line · diagnostic_overlay

placement.kind      wall · table · floor · object_anchored · above_target ·
                    near_user · near_presenter · left_of_user · right_of_user ·
                    in_front_of_user · room_center

anchorStrategy      world_anchor · plane_anchor · object_anchor ·
                    relative_to_target · user_relative · simulated_anchor

scaleMode           real_scale · tabletop_scale · enlarged_detail ·
                    compact_panel · room_scale

attentionBehavior   ambient · persistent_context · active_focus ·
                    peripheral · on_demand · guiding
```

These are published in-band on every contract under `vocabularies` so any
consumer (viewer, Vision Pro player, validator) can switch on them without
importing this codebase.

## The critical alignment rule

The Mustang demo asserts:

> "The exploded air filter assembly must align directly above the actual
> target housing. It should not be randomly tilted or floating vaguely."

This is enforced in `placement_engine.js#explodedAboveTarget`:

1. Physical targets are placed first (their priority puts them ahead of
   their dependents in the placement order).
2. When an `assembly_explode` element is placed, the engine reads the
   target element's position from the layout and sets its own position
   to `[targetX, targetY + 0.55, targetZ]` with `anchorStrategy = above_target`.
3. The viewer's `renderExplodedView` adds a dashed guide line from the
   target up through the stack so the relationship is unmistakable.

The Mustang contract proves this — the air filter assembly is at
`(0, 1.35, 0)` directly above the target housing at `(0, 0.8, 0)`.

## Demos

`demos/mustang-service-card.json` — 12 spatial elements proving the engine:

| # | Element                       | representationMode  | placement        | anchorStrategy     | scaleMode       | attentionBehavior   |
| - | ----------------------------- | ------------------- | ---------------- | ------------------ | --------------- | ------------------- |
| 1 | Vehicle Status                | `two_d_panel`       | `left_of_user`   | `user_relative`    | `compact_panel` | `persistent_context`|
| 2 | Maintenance Summary           | `two_d_panel`       | `left_of_user`   | `user_relative`    | `compact_panel` | `persistent_context`|
| 3 | Insurance & Roadside          | `two_d_panel`       | `left_of_user`   | `user_relative`    | `compact_panel` | `persistent_context`|
| 4 | Service History               | `two_d_panel`       | `left_of_user`   | `user_relative`    | `compact_panel` | `persistent_context`|
| 5 | Air Filter Housing on Car     | `highlighted_target`| `object_anchored`| `simulated_anchor` | `real_scale`    | `active_focus`      |
| 6 | Dirty Filter Finding          | `diagnostic_overlay`| `above_target`   | `relative_to_target`| `compact_panel`| `active_focus`      |
| 7 | Exploded Air Filter Assembly  | `exploded_view`     | `above_target`   | `relative_to_target`| `enlarged_detail`| `active_focus`     |
| 8 | Alignment Guide               | `guide_line`        | `above_target`   | `relative_to_target`| `real_scale`   | `guiding`           |
| 9 | Spring Clips (×4)             | `anchored_callout`  | `object_anchored`| `relative_to_target`| `compact_panel`| `guiding`           |
| 10| T20 Torx Screw                | `anchored_callout`  | `object_anchored`| `relative_to_target`| `compact_panel`| `guiding`           |
| 11| Repair Steps                  | `two_d_panel`       | `right_of_user`  | `user_relative`    | `compact_panel` | `active_focus`      |
| 12| Materials & Tools             | `two_d_panel`       | `right_of_user`  | `user_relative`    | `compact_panel` | `persistent_context`|

Relationships in the contract:

- `aligned_above`         exploded_air_filter_assembly → air_filter_housing
- `connects`              alignment_guide → air_filter_housing  *and*  alignment_guide → exploded_air_filter_assembly
- `attached_to`           spring_clips → air_filter_housing
- `attached_to`           t20_torx_screw → air_filter_housing
- `diagnoses`             dirty_filter_finding → air_filter_housing
- `controls_attention_for` repair_steps → air_filter_housing

Scene-wide interactions: `reset_view`, `next_step`, `previous_step`.
Per-element interactions match the user spec: `highlight_current_part`,
`isolate_target_part`, `explode_assembly`, `collapse_assembly`,
`tap_part_to_show_label`, `select_decision`, `orbit_around`.

`demos/q3-manufacturing-review-card.json` — proves the multi-mode mix:
- KPIs → wall dashboard
- cost breakdown summary → 2D panel
- factory floor process model → tabletop 3D model
- Q3 events timeline → walkable floor path
- recommended next actions → floating decision cards in user hand-reach

Both contracts live in `scene_contracts/`. The web viewer reads
`scene_contracts/_spatail_index.json` to populate the picker.

## How existing CAD ingestion plugs in

The legacy `scanner.js` / `classifier.js` / `blender_runner.js` are still
the path that turns dropped CAD into normalized GLBs. SPATAIL calls into
them through `ContentIngestionLayer.probeAssetGroups`. Any
`object3d` source that names an `assetGroupRef` matching a group under
`/assets_raw` gets the resolved group attached to its `requiredAssets`
entry. The web viewer currently renders placeholder geometry; the
visionOS player and the next web-viewer iteration will load the real GLBs
when present.

Legacy `npm run generate` + `viewer/index.html` still work unchanged — they
remain the CAD-only path that produces a `SpatialSceneContract.json` for a
single asset group. SPATAIL's contract is a superset.

## CLI

```bash
# Legacy CAD pipeline (unchanged)
npm run generate            # /assets_raw -> SpatialSceneContract.json
npm run viewer              # serves http://localhost:5173/

# New SPATAIL pipeline
npm run spatail             # demos/*-card.json -> scene_contracts/*-spatial-contract.json
npm run spatail:dev         # spatail + viewer in one shot
```

`http://localhost:5173/` defaults to the SPATAIL viewer. The legacy
single-CAD viewer is at `http://localhost:5173/viewer/index.html`.

## How this connects to a visionOS app

`visionos_export/README.md` already documents the contract-consumer
pattern. The SPATAIL contract is a strict superset of the legacy
contract: same versioned JSON, served from the same location, consumed
the same way.

To bring the visionOS player up to SPATAIL parity, the Swift runtime
needs to implement one renderer per representation mode:

| representationMode      | RealityKit equivalent                                |
| ----------------------- | ---------------------------------------------------- |
| `two_d_panel`           | `AttachmentEntity` with a SwiftUI `Text` panel       |
| `wall_dashboard`        | larger `AttachmentEntity` anchored to a plane anchor |
| `tabletop_model`        | `ModelEntity` anchored to the table plane            |
| `three_d_model`         | `ModelEntity` anywhere in the scene                  |
| `highlighted_target`    | `ModelEntity` with bright translucent material + outline |
| `exploded_view`         | parent `Entity` with vertically offset child models  |
| `anchored_callout`      | `AttachmentEntity` parented to its target entity     |
| `diagnostic_overlay`    | floated `AttachmentEntity` above the diagnosed target|
| `guide_line`            | `Entity` with a procedural dashed line mesh          |
| `floor_timeline`        | row of `ModelEntity` plates anchored to `floor`      |
| `floating_decision_card`| `AttachmentEntity` anchored relative to head pose    |

`element.placement.position` is in metres in the same coordinate frame
the planner uses; visionOS will reanchor to the user's real room
(`environmentAssumptions.surfaces` lists which anchors to request).

The contract's `vocabularies` block ships the closed enum sets in-band so
the Swift side doesn't need a separate schema file — it can switch on the
strings directly and fail fast on any future value it doesn't know.

## File layout (v0.2)

```
src/
├── planner/
│   ├── SpatialExperiencePlanner.js   # planExperience() + ingestCard()
│   ├── RepresentationSelector.js     # selectRepresentation()
│   ├── SpatialPlacementEngine.js     # placeElement() + createLayoutState()
│   └── ExperienceReasoning.js        # attentionBehaviorFor / priorityFor /
│                                     # interactionsFor / narrationFor /
│                                     # buildAttentionPlan / summarizeReasoning
└── types/
    └── SpatialExperienceContract.js  # JSDoc typedefs + re-exported enums

pipeline/spatail/
├── content_ingestion.js              # normalize { prompt, sources }
├── understanding.js                  # detect domain, classify contentType
├── representation_selector.js        # representation rules engine
├── placement_engine.js               # placement / anchor / scale strategies
├── experience_reasoning.js           # explainable per-element + scene reasoning
├── experience_contract.js            # contract schema + builders + closed enums
└── experience_planner.js             # orchestrates the four layers

schemas/
└── spatialExperienceContract.schema.json   # JSON Schema, mirrors the enums
```

`src/planner/*` and `src/types/*` are the stable public API. `pipeline/spatail/*`
is the implementation home; consumers should not import from there directly.

## What's still stubbed

| Area                                       | State |
| ------------------------------------------ | ----- |
| Viewer renders placeholder geometry for 3D elements (boxes, not loaded GLBs) | v0.3 — wire the existing GLB loader into the SPATAIL renderers when `requiredAssets.resolvedAssetGroup` is present |
| Card sources are JSON; no PDF / Confluence / Linear ingestion yet | v0.3 — add adapters that emit the same `{ prompt, sources }` shape |
| Domain detection is keyword-based          | v0.3 — swap `understanding.js#detectDomain` for an LLM call when needed |
| Placement is greedy + room-relative        | v0.3 — collision-aware solver; for now the room dimensions are generous |
| Asset retrieval is filename heuristic only | v0.3 — semantic match against `assetGroupRef` |
| visionOS player                            | v0.3 — see table above |
| Figma → uiElements bridge                  | v0.3 (already noted in legacy roadmap) |
