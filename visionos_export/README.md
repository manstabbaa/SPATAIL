# visionOS export

The Vision Pro app is **not built yet**. This folder pins the contract the
future runtime will consume so we don't drift away from it as v0.1 evolves.

## The deal

The visionOS player is a **runtime / player**, not an authoring tool.
Authoring lives in Spatial Studio (the web app in `/viewer`) plus the
Claude-driven pipeline in `/pipeline`. Everything heavy happens before
the app starts.

The player gets one folder, mirrored from this repo:

```
SpatialBundle/
├── SpatialSceneContract.json        # copied from /scene_contracts
└── assets/                          # copied from /assets_processed
    ├── <assetId>.glb
    ├── <assetId>.glb
    └── …
```

…and is responsible for:

1. parsing `SpatialSceneContract.json`
2. loading every asset whose `status == "processed"` from `assets/`
3. honoring `placement` (anchor type, scale mode, safe distance, facing)
4. honoring `orientationRules` (the contract guarantees `+Y` up, `-Z` forward
   after Blender normalization)
5. rendering one floating button per `uiElement` where
   `visibleIn` contains `"vision_pro"`
6. dispatching the matching `interactionBrick` on tap

The set of representation modes the visionOS player must render to be at
parity with the iPhone AR runtime + the Contract Studio:

| `representationMode`         | visionOS behavior                                |
| ---------------------------- | ------------------------------------------------ |
| `two_d_panel`                | `AttachmentEntity` + SwiftUI text panel, anchored to placement.kind |
| `wall_dashboard`             | larger `AttachmentEntity` anchored to a plane    |
| `three_d_model` / `tabletop_model` | `ModelEntity` (USDZ or placeholder) on the chosen anchor |
| `highlighted_target`         | `ModelEntity` with bright translucent shader + outline |
| `exploded_view`              | parent `Entity` with vertically offset child models above the target |
| `anchored_callout`           | `AttachmentEntity` parented to the target entity |
| `diagnostic_overlay`         | floated `AttachmentEntity` above the diagnosed target |
| `guide_line`                 | procedural dashed-line `Entity` using `placement.from` / `placement.to` |
| `floor_timeline`             | row of `ModelEntity` plates anchored to the floor |
| `floating_decision_card`     | `AttachmentEntity` anchored relative to head pose |

The scene-wide interaction set the player must wire up:

| `interaction.type` | behavior                                                    |
| ------------------ | ----------------------------------------------------------- |
| `reset_view`       | restore camera + transforms + materials + visibility        |
| `next_step`        | advance the attention plan to the next focusElementId       |
| `previous_step`    | step the attention plan back                                |
| `highlight`        | re-tint the target (used by per-element `highlight_current_part`) |
| `isolate`          | hide every element whose role isn't the brick target        |
| `explode`/`collapse` | toggle the exploded-view offsets                          |
| `expand` (tap)     | open a fuller panel for the tapped element                  |
| `select`           | choose a floating_decision_card option                      |

New modes / interactions land in both the visionOS player and the iPhone
AR runtime at the same time — that's the whole point of the shared
`SpatialExperienceContract`.

## Why not generate code from the contract

We considered emitting Swift from the contract. Don't. The contract is
**data** and the player should remain a hand-written runtime that reads it.
Generated player code rots faster than the contract does.

## Toolchain notes (for whoever builds it)

- Xcode + visionOS SDK
- RealityKit for the 3D content (`ModelEntity` per asset, `AnchorEntity` per placement type)
- SwiftUI for the floating button panel(s)
- glTF/GLB loading: convert to USDZ at build-time via Apple's `usdzconvert`,
  or load GLB at runtime via a third-party loader. The pipeline already
  produces clean GLBs — USDZ conversion belongs *in the player build*, not
  in this repo.

## v0.2 milestone

A barebones visionOS app that:

1. takes the bundle directory as input
2. opens the scene full-immersion with the model centered 1.5 m in front of the user
3. shows the four default buttons
4. wires them through to the four default bricks

That's a single sprint of Swift work. The contract is already correct.
