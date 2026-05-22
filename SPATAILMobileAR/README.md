# SPATAILMobileAR — iPhone AR prototype

iPhone AR demo surface for SPATAIL spatial experiences. Same
`SpatialExperienceContract.json` the web viewer consumes — rendered
through ARKit + RealityKit instead of Three.js. The on-device renderer
treats every element as a future-Vision-Pro element: content lives in
real space; 2D panels are spatially placed, not screen overlays; 3D
objects are anchored in the environment; the user moves around the
experience.

## Status

- ✅ Source layout, MVVM split, contract decoder, demo selector,
  pre-AR explanation view, AR coordinator with tap-to-place, per-mode
  RealityKit builders for every representation mode the contract emits.
- ⚠️ **Not yet compiled.** This scaffold was authored on Windows. Open
  it on a Mac, follow the setup below, and expect to fix a small
  handful of API-name issues on first build (RealityKit + ARKit APIs
  change between Xcode versions). The architecture and the contract
  decoder are stable.

## Setup (Mac, one-time)

1. Install **Xcode 16.3+** (Swift 6.1 — the source uses trailing commas
   in function-argument lists, SE-0439). On older toolchains, strip
   trailing commas before `)` in any function/init call.
2. Install XcodeGen: `brew install xcodegen`
3. From this folder: `xcodegen generate` — produces `SPATAILMobileAR.xcodeproj`.
4. Open `SPATAILMobileAR.xcodeproj` in Xcode.
5. Signing & Capabilities → set your Apple Developer team.
6. Build & run on a physical iPhone (ARKit does not run in the simulator).

> If you don't want XcodeGen: create a new SwiftUI iOS App named
> `SPATAILMobileAR` in Xcode, then drag the `App/`, `Models/`,
> `Services/`, `Views/`, `Reality/`, and `Resources/` folders into the
> project. Add `NSCameraUsageDescription` to Info.plist.

## Bundled contracts

`Resources/mustang-service-spatial-contract.json` and
`Resources/q3-manufacturing-review-spatial-contract.json` are copied
verbatim from the web backend (`/scene_contracts`). To refresh them
after editing demo cards or the planner:

```bash
# From the project root:
npm run spatail:ios
```

…which regenerates contracts and copies the latest JSON into this folder
in one step.

## App flow

1. **DemoSelectorView** — clean splash with two cards:
   *Mustang Service Assistant* and *Q3 Manufacturing Cost Review*.
2. **GeneratedExperienceView** — the bridge before AR. Shows the source
   prompt, the source card contents, and the full spatial plan with
   per-element reasoning (`whyThisRepresentation` + `whyThisPlacement`).
   This is where the Vision Pro design intent is visible *before* you
   put the phone up.
3. **ARExperienceContainerView** — full-screen ARKit view. Coaching
   overlay guides the user to scan a horizontal surface. Tap to place.
4. **The scene** — every element from the contract becomes a
   RealityKit entity at its contract-specified position. The phone UI
   is intentionally minimal: a Reset / Next-step / Explode-toggle
   pill at the bottom and the active attention-plan narration at the
   top. The actual content lives in the AR world.

## Vision Pro design through an iPhone lens

The renderers are 1:1 with the modes the visionOS player will need:

| Contract `representationMode`     | RealityKit renderer (this app)             | visionOS equivalent              |
| --------------------------------- | ------------------------------------------ | -------------------------------- |
| `two_d_panel` / `wall_dashboard`  | `SpatialPanelBuilder` / `WallPanelBuilder` | `AttachmentEntity` + SwiftUI     |
| `three_d_model` / `tabletop_model`| `TabletopModelBuilder`                     | `ModelEntity` on table anchor    |
| `highlighted_target`              | `AnchoredObjectBuilder` + `HighlightMaterialFactory` | same                   |
| `exploded_view`                   | `AirFilterAssemblyBuilder`                 | parented vertical-offset entities|
| `anchored_callout`                | `CalloutBuilder`                           | attachment parented to target    |
| `diagnostic_overlay`              | `SpatialPanelBuilder` (`.diagnostic` style)| floated attachment over target   |
| `guide_line`                      | `GuideLineBuilder`                         | procedural dashed-line entity    |
| `floor_timeline`                  | `FloorTimelineBuilder`                     | row of model entities on floor   |
| `floating_decision_card`          | `SpatialPanelBuilder` (`.decision` style)  | head-relative AttachmentEntity   |

Same JSON in, same logical scene out, two different platforms. When
the visionOS player ships, it should reuse the contract bytes
unchanged.

## Critical alignment rule (Mustang)

The contract puts the air-filter housing at `(0, 0.8, 0)` and the
exploded assembly directly above at `(0, 1.35, 0)` — identical X/Z, only Y
offset. The contract also emits a dedicated `guide_line` element whose
endpoints (`placement.from` / `placement.to`) are pre-resolved to the
positions of the housing and the assembly, so `GuideLineBuilder` draws
the connecting dashed line from data alone. This makes the part-to-
position mapping obvious in AR, just as it does in the web Contract Studio.

## Architectural intent

- **MVVM.** SwiftUI views own no business logic. Services are the
  collaboration layer; Views are projections of state.
- **Services are thin.** For v1 the on-device planner is a pass-through
  that loads contracts the backend already produced. The interfaces
  exist so we can move the planning logic on-device later (LLM call
  + local representation/placement) without rewriting the views.
- **Reality is decoupled.** Every builder takes a `SpatialElement` and
  returns an `Entity`. The coordinator is the only place that knows
  about ARKit; everything else is testable without AR.

## What's stubbed (intentional)

- Placeholder geometry for 3D elements — no GLB / USDZ loading yet.
- No real camera object recognition — the engine bay is a simulated
  base mesh, not detection-driven.
- `PromptIntentClassifier`, on-device `RepresentationSelector`, and
  `SpatialPlacementEngine` are stubs that route through the bundled
  contract for v1.
- Wall anchoring uses vertical-plane detection where available and
  falls back to a fixed offset from the user. No persisted ARWorldMap
  yet.

## Next step

Wire `VehicleKnowledgeService` + `SimulatedDiagnosticsService` to a
mocked agent loop so a user can type "help me service my Mustang"
directly in `ContentCardInputView`, get a generated card, generate a
plan in-process, and launch the AR preview — all without the
backend's `npm run spatail` step. That makes the iPhone a fully
self-contained SPATAIL demo for v1.
