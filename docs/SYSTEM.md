# SPATAIL — System

SPATAIL is a **runtime-on-demand spatial system**: a prompt, photo, object, or document
becomes an interactive XR experience that is placed, styled, and made interactive in the
real world. Education is **one domain** on top of this runtime, not the product itself.

The system is organized around five pillars. This document maps each pillar to the code
that implements it today, and marks the gaps. For the retirement ledger of superseded
subsystems, see [LEGACY.md](LEGACY.md).

---

## MATTER — why / what / how it appears

| Sub-pillar | What it means | Lives in |
|---|---|---|
| **Why** | intent · domain · subject; the story that decides what to show | `studio/director/asset_brief.py`, `studio/director/composer.py`, `studio/representation/{engine,intent_classifier,domain_classifier,taxonomy}.py` |
| **What** | the asset that appears, produced and resolvable | `studio/meshy/asset_service.py`, `studio/meshy/pipeline.py`, `studio/meshy/regions.py`, `studio/library/asset_library.py` |
| **How** | the visual treatment at runtime | `ios/Spatail/Sources/SpatailMaterials.swift` (runtime master-material effects), `studio/blender/spatail_style.py` (clay render finish) |

## SPACE — mapping / placement / takeover

| Sub-pillar | What it means | Lives in |
|---|---|---|
| **Mapping** | the room understood (surfaces, obstacles, user) | `studio/spatail/room_model.py`, `ios/Spatail/Sources/RoomModel.swift`, `ObjectAnchoringController.swift` (tracked stream) |
| **Placement principles** | anchor · scale · comfort · collision policy + solver | `studio/spatail/{placement_solver,design_system,object_size,analysis}.py`, `studio/xr_design.py`, `ios/Spatail/Sources/PlacementSolver.swift` |
| **Spatial takeover** | full-environment / large-scale takeover | **GAP** — placement is surface-bound (table/floor) only |

## IDENTITY — brand guide / render style / personalization

| Sub-pillar | What it means | Lives in |
|---|---|---|
| **Render style guide** | the asset look | `studio/blender/spatail_style.py` (Peter-Tarka clay finish) |
| **Brand graphic guide** | the *user's* brand becomes the experience | **GAP** — `studio/director/scene_contract.py` emits `brand={status:'pending'}` |
| **Personalization** | per-user / per-org style, palette, voice, layout | **GAP** — no brand capture, schema, or tokens exist |

> The manifesto is explicit: *"the presentation should feel like your brand, not ours."*
> IDENTITY is the system's biggest white space and the clearest place to build next.

## INTERACTION — rules / behaviours / gestures

| Sub-pillar | What it means | Lives in |
|---|---|---|
| **Interaction rules** | the trigger graph / game manager | `studio/director/logic.py`, `ios/Spatail/Sources/ModularRuntime.swift` |
| **Object behaviours** | baked animation · articulation · growth | `studio/meshy/{animate,grow,skeleton}.py` |
| **Gestures** | pinch · drag · two-hand · manipulate | **THIN** — tap-driven defaults only |

## CREATION — pipeline / craft / build

| Sub-pillar | What it means | Lives in |
|---|---|---|
| **Pipeline** | the on-demand producer, end to end | `studio/server/job_server.py`, `studio/server/headless_build.py`, `studio/director/experience.py`, `studio/vision/driver.py` |
| **Craft** | Blender authoring: build, anchors, evidence, apply | `studio/blender/{build_studio,realworld,motion}.py`, `studio/meshy/anchors.py`, `studio/vision/{evidence,apply}.py` |
| **Build / serve** | Blender → SPATAIL → iOS handoff | `studio/ios_sync.py`, `studio/asset_manifest.py`, repo-root `asset_factory/` (GLB ingest), `cowork-plugin/` (asset ops) |

---

## Gaps, in priority order

1. **IDENTITY / brand + personalization** — unbuilt. Needs a brand schema, capture, and
   tokens that flow into `scene_contract.py` and the iOS runtime.
2. **SPACE / spatial takeover** — only surface-seating exists; no environment-scale takeover.
3. **INTERACTION / gestures** — no first-class gesture vocabulary on either side.

## Proposed target layout (not yet applied)

A clean structure that mirrors the pillars (see LEGACY.md before moving anything):

```
engine/
  matter/        director (asset_brief, composer) + representation brain
  space/         spatail/{room_model, placement_solver, design_system, ...} + xr_design
  identity/      spatail_style + NEW brand/ + personalization/   ← fills the gap
  interaction/   director/logic + behaviours (animate, grow, skeleton) + gestures/
creation/
  pipeline/      server/* + director/experience + scene_contract
  craft/         blender/* + meshy/anchors + vision/*
  bridge/        ios_sync + asset_manifest
apps/spatail/    the iOS / visionOS app   (renamed from SpatailEducator ✓)
runtime/ios/     on-device engine mirrors (RoomModel, PlacementSolver, ModularRuntime, ...)
_legacy/         engineexplainer, pipeline/blender procedural, representation lineage
```
