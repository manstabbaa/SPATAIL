# visionOS object tracking API (later SPATAIL target — Vision Pro)

Sources: ObjectTrackingProvider API reference + "Exploring object tracking with ARKit"
sample (visionOS 27 beta) — captured 2026-06-10.

## Core types
- `ObjectTrackingProvider(referenceObjects:[ReferenceObject], trackingConfiguration:?)` —
  ARKit DataProvider; run via `ARKitSession.run([provider])`. visionOS 2+.
  - `anchorUpdates: AnchorUpdateSequence<ObjectAnchor>` — async stream (added/updated/removed).
  - `allAnchors: [ObjectAnchor]`, `state`, `isSupported`, `requiredAuthorizations`.
- `ReferenceObject(from: URL, configuration: ReferenceObject.Configuration)` —
  `configuration.highFrameRateTrackingEnabled = true` opts THIS object into high-rate
  tracking (visionOS 27; runtime setting, works with any previously trained file).
- `ObjectAnchor` — the tracked pose; `anchor.referenceObject.id` keys back to the source.
- **Metric pose (visionOS 27)**: `anchor.coordinateSpace(correction: .rendered)` =
  display-corrected (content visually glued to the object); `.none` = true metric pose
  (measurement). Sample reads `metricSpace.ancestorFromSpaceTransformFloat().translation`.

## Constraints
- Object tracking runs **only in an ImmersiveSpace** (silent failure elsewhere).
- Real device only (no simulator support).
- Authorization required (check `requiredAuthorizations`).

## Sample app pattern (Exploring object tracking with ARKit)
```swift
let objectTracking = ObjectTrackingProvider(referenceObjects: referenceObjects)
try await arkitSession.run([objectTracking])

for await update in objectTracking.anchorUpdates {
    switch update.event {
    case .added:
        // build visualization (embedded USDZ wireframe or bounding box), add to RealityView root
    case .updated:
        // move visualization to update.anchor's pose
    case .removed:
        // remove visualization
    }
}
```
The sample also demonstrates loading `.referenceobject` files at RUNTIME via fileImporter
(security-scoped URLs) — post-compile delivery is sanctioned.

## visionOS 27 RealityKit companions (same release, from session/WWDC26 coverage)
- Physical space lighting (virtual lights illuminate real surroundings) + projective textures.
- Gaussian splat rendering; real-time cloth simulation; acoustic ray tracing.
- (Spatial accessories: GCSpatialAccessory + AccessoryTrackingProvider — out of SPATAIL scope.)

## iOS ↔ visionOS portability
`.referenceobject` files are platform-neutral. SPATAIL's tracked-mode runtime should isolate
the anchoring layer: iOS = ARSessionDelegate/ARObjectAnchor; visionOS = ObjectTrackingProvider
/anchorUpdates. Everything above the anchor (experience root, clips, sequence, triggers)
is shared RealityKit.
