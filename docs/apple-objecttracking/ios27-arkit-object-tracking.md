# iOS 27 ARKit object tracking (the iPhone runtime — SPATAIL's primary target)

Source: "Using a reference object with ARKit in iOS" + session 283 (captured 2026-06-10).
iOS 27+ on iPhone/iPad. Same `.referenceobject` files as visionOS — no retraining.

## API surface
- `ARReferenceObject(archiveURL:)` — load a `.referenceobject` from any local file URL
  (bundle OR downloaded to Documents — runtime delivery works).
- `ARWorldTrackingConfiguration.detectionObjects: [ARReferenceObject]` — for mostly
  **stationary** objects. Stable world-space pose, low power, overlay may lag if moved.
- `ARWorldTrackingConfiguration.trackingObjects: [ARReferenceObject]` — for **moving /
  handheld** objects. Per-frame pose at the videoFormat frame rate; significantly higher
  power. (This property is the iOS expression of "high frame rate tracking".)
- Limit: **max 10 reference objects** combined across both properties per session.
- Legacy `.arobject` (iOS 12 point-cloud scans) only works with `detectionObjects` and
  cannot be mixed with `.referenceobject` in the same session.
- `ARObjectAnchor` — delivered through `ARSessionDelegate`; `isTracked` flags visibility.
- Attach content: `AnchorEntity(anchor: objectAnchor)` → `arView.scene.addAnchor(...)`;
  RealityKit keeps the entity glued to the tracked pose.
- `referenceObject.usdzFile` — embedded training USDZ (nil if stripped at build).

## Canonical pattern (from session slide + doc)
```swift
import ARKit
import RealityKit

final class ObjectTrackingARSessionDelegate: NSObject, ARSessionDelegate {
    let arView = ARView(frame: .zero)
    var entities: [UUID: AnchorEntity] = [:]

    func start() throws {
        let stationary = try ARReferenceObject(archiveURL: stationaryURL)
        let moving     = try ARReferenceObject(archiveURL: movingURL)

        let config = ARWorldTrackingConfiguration()
        config.detectionObjects = [stationary]   // low frame rate, low power
        config.trackingObjects  = [moving]       // high frame rate

        arView.session.delegate = self
        arView.session.run(config)
    }

    func session(_ session: ARSession, didAdd anchors: [ARAnchor]) {
        for case let anchor as ARObjectAnchor in anchors {
            let entity = AnchorEntity(anchor: anchor)
            entities[anchor.identifier] = entity
            arView.scene.addAnchor(entity)
            // SPATAIL: mount the experience root (Blender-exported USDZ) here.
        }
    }

    func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) {
        for case let anchor as ARObjectAnchor in anchors {
            entities[anchor.identifier]?.isEnabled = anchor.isTracked   // hide, don't remove
        }
    }

    func session(_ session: ARSession, didRemove anchors: [ARAnchor]) {
        for case let anchor as ARObjectAnchor in anchors {
            if let entity = entities.removeValue(forKey: anchor.identifier) {
                arView.scene.removeAnchor(entity)
            }
        }
    }
}
```

## Operational notes
- ARKit does NOT auto-remove object anchors; the app owns lifecycle.
- `isTracked == false` (object left FOV): disable the entity instead of removing → instant
  reappearance when re-acquired.
- `trackingObjects` battery cost is real — only promote an object to tracking (vs detection)
  when the experience needs per-frame glue (handheld demos).
- Coexists with the world-tracking config SPATAIL already runs (planes, raycasts, world
  anchors) — tracked mode and placed mode share one `ARWorldTrackingConfiguration` session.
