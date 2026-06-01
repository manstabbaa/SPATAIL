# Placing Content on Detected Planes (ARKit / visionOS)
Source: https://developer.apple.com/documentation/visionos/placing-content-on-detected-planes
Captured verbatim for SPATAIL — how content attaches to tables/floors/walls.

## Overview
Flat surfaces are ideal for positioning content in a Full Space. Use **plane detection** in ARKit to detect surfaces and filter by size, proximity, or orientation.

## Basic plane anchoring with RealityKit AnchorEntity (no permission prompt)
If you don't need a *specific* plane and you render in RealityKit, use an **`AnchorEntity`** — attach 3D content to a plane without world-sensing permission and without knowing where the plane is:

```swift
AnchorEntity(.plane(.horizontal, classification: .table, minimumBounds: [0.5, 0.5]))
```
Anchor entities ask for a plane with certain characteristics (not a specific one). For specific selection or real-time pose, use `ARKitSession` + `PlaneDetectionProvider`.

## Configure an ARKit session for plane detection (needs worldSensing auth)
Detect horizontal, vertical, or both. Each plane has a classification (`table`, `floor`, `wall`, `window`, …):

```swift
let session = ARKitSession()
let planeData = PlaneDetectionProvider(alignments: [.horizontal, .vertical])
Task {
    try await session.run([planeData])
    for await update in planeData.anchorUpdates {
        if update.anchor.classification == .window { continue }   // skip windows
        switch update.event {
        case .added, .updated: await updatePlane(update.anchor)
        case .removed:         await removePlane(update.anchor)
        }
    }
}
```

## Create/update entities per plane
Update content when ARKit sends new info; remove content on removal events:

```swift
@MainActor var planeAnchors: [UUID: PlaneAnchor] = [:]
@MainActor var entityMap: [UUID: Entity] = [:]

@MainActor func updatePlane(_ anchor: PlaneAnchor) {
    if planeAnchors[anchor.id] == nil {
        let entity = ModelEntity(mesh: .generateText(anchor.classification.description))
        entityMap[anchor.id] = entity
        rootEntity.addChild(entity)
    }
    entityMap[anchor.id]?.transform = Transform(matrix: anchor.originFromAnchorTransform)
}
@MainActor func removePlane(_ anchor: PlaneAnchor) {
    entityMap[anchor.id]?.removeFromParent()
    entityMap.removeValue(forKey: anchor.id)
    planeAnchors.removeValue(forKey: anchor.id)
}
```

---
### SPATAIL takeaways
- `AnchorEntity(.plane(.horizontal, classification: .table, minimumBounds:[w,h]))` is the
  one-liner to sit an experience on the user's real table — no permission prompt. Great default.
- Plane classifications (table/floor/wall) feed which stations go where (floor demo vs wall panel).
- visionOS uses PlaneDetectionProvider; our iOS app uses ARKit plane detection — keep placement
  abstracted behind RoomProfile so the spec maps to both.
