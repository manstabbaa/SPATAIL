# Physics + Gestures in RealityKit (the "live mechanics" toolkit)
Sources (via tutorials/data JSON):
- https://developer.apple.com/documentation/realitykit/physics-simulations-and-motion
- https://developer.apple.com/documentation/realitykit/responding-to-gestures-on-an-entity
Captured for SPATAIL — the API surface our Mechanic Library wraps.

## Physics simulations and motion
RealityKit simulates interactions between virtual objects, and between virtual objects and detected real surfaces (floors/walls/tables); with LiDAR, against scanned geometry.

**Physical properties (components/resources):**
- **`PhysicsBodyComponent`** — defines an entity's behavior in physics simulations.
- **`PhysicsBodyMode`** — how a body moves in response to forces:
  - `static` (immovable, e.g. ground), `kinematic` (moved by code, not forces), `dynamic` (full physics).
- **`PhysicsMaterialResource`** — material properties like **friction** and restitution (bounciness).
- **`PhysicsMassProperties`** — mass / center of mass / inertia.
- **`PhysicsMotionComponent`** — controls the body's linear/angular velocity.
- **`ImpulseAction`** — applies an impulse at the center of mass (as a playable animation).

**Compliance interfaces:** `HasPhysicsBody`, `HasPhysicsMotion`, `HasPhysics`.

**Setup / scaling:**
- Designing scene hierarchies for efficient physics simulation.
- Handling different-sized objects in physics simulations.
- `PhysicsSimulationComponent` — controls localized simulations.
- `PhysicsSimulationEvents` — events during simulation.

**Related:** Collision detection · Force effects · Physics joints and pins · Particle simulation (`ParticleEmitterComponent`, presets).

### → maps to the user's "rigid is rigid, gooey is gooey" idea
- rigid → `dynamic` body + high friction + low restitution
- bouncy → high restitution
- soft/gooey → joints/pins or particle/soft approximations (start with material params)
- "slap physics on any object" → attach `PhysicsBodyComponent` + `CollisionComponent` at runtime.

## Responding to gestures on an entity
To receive `RealityView` gesture events, an entity needs:
1. **`InputTargetComponent`** — marks the entity as participating in the event system.
2. **`CollisionComponent`** — the shape the gaze vector tests against.
3. (optional) **`HoverEffectComponent`** — highlights as gaze intersects.

```swift
var cube = ModelEntity(mesh: .generateBox(size: 0.1),
                       materials: [SimpleMaterial(color: .orange, isMetallic: false)])
cube.components.set(InputTargetComponent())
cube.components.set(CollisionComponent(shapes: [.generateBox(size: SIMD3(0.1,0.1,0.1))]))
cube.components.set(HoverEffectComponent())
```
Gesture (gaze + pinch) targeting any entity:
```swift
.gesture(SpatialEventGesture().targetedToAnyEntity().onEnded { value in
    value.entity.components[ActiveComponent.self]?.active.toggle()
})
```
**`BillboardComponent`** makes an entity (e.g. a text attachment) always face the person. Attachment shown/hidden by toggling child membership in the `update` closure; positioned with `setPosition(_, relativeTo:)`.

Gesture types available: `TapGesture` / `SpatialTapGesture`, `DragGesture`, `MagnifyGesture` (scale), `RotateGesture`/`RotateGesture3D`, `SpatialEventGesture` — all via `.targetedToAnyEntity()` or `.targetedToEntity(_:)`.

---
### SPATAIL takeaways (Mechanic Library v1)
- **Grab** = DragGesture.targetedToAnyEntity + InputTargetComponent + CollisionComponent.
- **Grab+Physics** = above + PhysicsBodyComponent(.dynamic) + PhysicsMaterialResource.
- **Tap-Reveal** = SpatialTapGesture toggling a BillboardComponent text attachment (panel).
- **Play-Baked** = just play the entity's USDZ AnimationResource (no gestures needed).
- Every mechanic is "a set of components + a gesture" → that's literally our interchangeable-module shape.
