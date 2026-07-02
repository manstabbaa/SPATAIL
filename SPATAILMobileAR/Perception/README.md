# Perception → Placement pipeline

Live camera-driven spatial computing for SPATAILMobileAR. Wires the SPATAIL
principle as four decoupled stages:

> **Vision** detects WHAT → **ARKit** resolves WHERE → **SPATAIL** decides
> WHAT APPEARS → **RealityKit** renders & anchors.

This module is **additive** — it sits alongside the existing
`ARExperienceCoordinator` (which is untouched) and is reached from the
"Live Perception" card on the demo selector. It maps directly onto the
2026 Master File: scene context = the semantic RoomModel (p.22); placement
is spatial reference not coordinates (p.26); recognition + reconstruction
(p.28); locus of control (p.29); behavior contract (p.42).

## Data flow

```
ARFrame.capturedImage
   │
   ▼  Detection/              ── "WHAT"  (AR-free)
DetectionService ─┬─ MockDetectionService        (center box, validates the pipeline)
                  ├─ VisionDetectionService       (REAL, no model — Apple Vision built-ins)
                  └─ CoreMLVisionDetectionService  (REAL, custom .mlmodel when present)
   │  → [SpatailDetection]  (normalized box, worldHit == nil)
   ▼  Spatial/               ── "WHERE"  (the 2D→3D boundary)
SpatialResolver ─ depth (DepthSampler/LiDAR) → plane raycast → camera-ray fallback
   │  → [SpatailDetection]  (worldHit set: position, normal, anchorType, confidence)
   ▼  Contract/
SpatailPerceptionFrame  { cameraTransform, detections, sceneContext }   ← Codable packet
   │
   ▼  Engine/                ── "WHAT APPEARS"  (pure, AR-free, testable)
PerceptionPlacementEngine  (intent classify → behavior contract per detection)
   │  + PerceptionNarrator (offline copy) + AssetCatalogBuilder (usdz match)
   │  → SpatailPlacementPlan  { placements[] each with its REASON }   ← Codable packet
   ▼  Runtime/               ── "RENDER + ANCHOR"
PerceptionPlacementRuntime  (diff anchors by stable id, billboard, USDZ async load)
   ▼
Debug/  PerceptionDebugOverlay  (2D boxes + decision/tracking HUD)
Session/ LivePerceptionCoordinator  (the 1 Hz live loop; SceneEvents.Update tick)
```

## Folder map

| Folder | Files | Imports AR? |
|---|---|---|
| `Contract/` | `SpatailPerceptionContract`, `SpatailPlacementContract` | No (Foundation + simd) |
| `Detection/` | `DetectionService` (protocol + source switch), `Mock`, `Vision`, `CoreMLVision` | No (Vision/CoreML, not ARKit) |
| `Spatial/` | `SpatialResolver`, `DepthSampler` | **Yes** (the 2D→3D bridge) |
| `Engine/` | `PerceptionPlacementEngine`, `PerceptionNarrator`, `AssetCatalogBuilder` | No (pure brain) |
| `Runtime/` | `PerceptionPlacementRuntime` | **Yes** (RealityKit) |
| `Session/` | `LivePerceptionCoordinator` | **Yes** (ARView host + loop) |
| `Debug/` | `PerceptionDebugModel`, `PerceptionDebugOverlay` | No (SwiftUI) |

The screen is `Views/LivePerceptionView.swift`; the demo entry is in
`Views/DemoSelectorView.swift`; the folder is added to `project.yml`.

## Detectors

Switch at runtime in the live view (segmented control):

- **Mock** — one center detection per tick; validates the whole pipeline.
- **Vision** — REAL, works today with no setup: Apple Vision saliency +
  whole-image classification + animal + human detectors.
- **Core ML** — REAL custom model: drop a compiled `ObjectDetector.mlmodelc`
  (Create ML object detector or YOLO export) into the bundle; activates with
  no code change.

## What still needs an external input (not code)

- A custom object-detection **`.mlmodel`** (only if you want a domain model;
  Vision works without one).
- A server **LLM** for richer intent + explanation copy (the
  `PerceptionNarrator` / `IntentClassifier` seams).
- Bundled / downloaded **USDZ** assets for real 3D models (the loader and
  catalog are already live; absent assets → placeholder boxes).

## Notes

Authored on Windows; build on a Mac (`xcodegen generate`, run on a physical
iPhone — ARKit needs a device). A few lines carry `SPATAIL_NEEDS_MAC_BUILD_VERIFY`
per the app's house convention. Verified by two adversarial multi-agent review
passes (no compiler on the authoring machine).
