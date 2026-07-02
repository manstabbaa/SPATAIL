# Combining 2D and 3D Views — RealityView Attachments (RealityKit)
Source: https://developer.apple.com/documentation/realitykit/combining-2d-and-3d-views-in-an-immersive-app
Captured verbatim for SPATAIL — THE mechanism for our spatial UI panels (2D labels/panels on 3D).

## Overview
Place 2D content (SwiftUI/UIKit views) relative to 3D entities in an immersive space, including positioning an attachment at the location of a tap. Sample = a rainbow of USDZ arches + `RealityViewAttachment`s; "cloud" attachments appear at tap locations.

## Load + configure entities (set material on ModelComponent)
```swift
func createEntity(for item: EntityData) async -> Entity {
    let rc = try! await Entity(named: item.title, in: realityKitContentBundle)
    guard let modelEntity = rc.findEntity(named: item.title),
          var modelComponent = modelEntity.components[ModelComponent.self] else { return Entity() }
    if let material = item.simpleMaterial { modelComponent.materials = [material] }
    modelEntity.components.set(modelComponent)
    return modelEntity
}
```

## Create attachments containing SwiftUI/UIKit views
In the `attachments` closure of a RealityView, iterate and create `Attachment(id:)` views. Attachments can hold SwiftUI views OR UIKit via `UIViewRepresentable`.
```swift
ForEach(rainbowModel.archAttachments) { entity in
    Attachment(id: "\(entity.title.rawValue)ArchAttachmentEntity") {
        createArchAttachment(for: entity.title)   // returns some View
    }
}
```

## Add + position attachments (in the update closure)
```swift
for va in rainbowModel.archAttachments {
    if let attachment = attachments.entity(for: "\(va.title)ArchAttachmentEntity") {
        plane?.addChild(attachment)
        attachment.scale = va.scale
        attachment.setPosition(va.position, relativeTo: yellowArch)
    }
}
```
Positions/scales can be derived from another entity's `BoundingBox` (e.g. each arch pushed back 0.1 m and scaled to 75% of the previous).

## Position an attachment at the TAP location
1. Configure entity for tap: `HoverEffectComponent`, `CollisionComponent` (static mesh from the model mesh), `InputTargetComponent`:
```swift
func configureForTapGesture(entity: Entity) async {
    entity.components.set(HoverEffectComponent())
    guard let mc = entity.components[ModelComponent.self] else { return }
    let shape = try! await ShapeResource.generateStaticMesh(from: mc.mesh)
    entity.components.set(CollisionComponent(shapes: [shape]))
    entity.components.set(InputTargetComponent())
}
```
2. Add a `SpatialTapGesture().targetedToAnyEntity()` and convert the tap into scene space:
```swift
.simultaneousGesture(
  SpatialTapGesture().targetedToAnyEntity().onEnded { value in
    var loc = value.convert(value.location3D, from: .local, to: .scene)
    loc.z += 0.02   // nudge forward so it doesn't overlap the entity
    rainbowModel.tapAttachments.append(CloudTapAttachment(position: loc, parent: nil))
  })
```
3. Create the tap attachment in `attachments`, then add + position it in `update`.

---
### SPATAIL takeaways (spatial UI panels)
- **Attachments are how a station's WRITTEN panel rides next to its 3D hero object** — exactly "stuff better written → spatial UI, stuff better seen → in the scene."
- `glassBackgroundEffect` + `BillboardComponent` (from responding-to-gestures) = a readable panel that always faces the learner.
- Tap-to-place attachment = the "reveal a label where you tapped" interaction for inquiry hooks.
