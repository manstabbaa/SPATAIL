# Adding 3D Content to Your App (visionOS)
Source: https://developer.apple.com/documentation/visionos/adding-3d-content-to-your-app
Captured verbatim for SPATAIL — the core "how to display + interact with 3D" article.

**Abstract:** Add depth and dimension to your visionOS app and incorporate content into a person's surroundings.

## Overview
The system provides several ways to display 3D content: in existing windows, in a volume, and in an immersive space. Choose what works for your content.

## Add depth to 2D windows
- `shadow` / `visualEffect` modifiers; `hoverEffect` on look; `ZStack` layout; `transform3DEffect`; `rotation3DEffect`.
- **`Model3D`** loads a USDZ (or other asset) and displays it at intrinsic size in a window. Good when you already have/can download the model (e.g. a 3D product).

## Display dynamic 3D scenes using RealityKit
RealityKit builds/updates 3D models dynamically. Use RealityKit + SwiftUI together. Load USDZ assets or Reality Composer Pro scenes (animation, physics, lighting, sounds, custom behaviors).

Use **`RealityView`** as the SwiftUI container for RealityKit content:

```swift
struct SphereView: View {
    var body: some View {
        RealityView { content in
            let model = ModelEntity(
                mesh: .generateSphere(radius: 0.1),
                materials: [SimpleMaterial(color: .white, isMetallic: true)])
            content.add(model)
        }
    }
}
```

The creation closure runs **once** (entity creation is expensive). To update, change view state and use an `update:` closure:

```swift
RealityView { content in
    // create once
} update: { content in
    if let model = content.entities.first {
        model.transform.scale = scale ? [1.2,1.2,1.2] : [1.0,1.0,1.0]
    }
}
```

## Respond to interactions with RealityKit content  ← KEY for our mechanics
To handle interactions with entities:
1. Attach a gesture recognizer to your `RealityView` and add **`.targetedToAnyEntity()`**.
2. Attach an **`InputTargetComponent`** to the entity (or a parent).
3. Add **collision shapes** (`CollisionComponent`) to entities that support interaction.

```swift
RealityView { content in
    let model = ModelEntity(
        mesh: .generateSphere(radius: 0.1),
        materials: [SimpleMaterial(color: .white, isMetallic: true)])
    model.components.set(InputTargetComponent())
    model.components.set(CollisionComponent(shapes: [.generateSphere(radius: 0.1)]))
    content.add(model)
}
.gesture(TapGesture().targetedToAnyEntity().onEnded { _ in scale.toggle() })
```
*If you omit InputTargetComponent + CollisionComponent, interactions are NOT detected.*

## Display 3D content in a Volume
A **volume** grows in 3D to match its content; windows *clip* 3D content that extends too far, so volumes are better for primarily-3D content. Create with a `WindowGroup` styled `.volumetric`:

```swift
WindowGroup { Model3D("balloons") }.windowStyle(style: .volumetric)
```
*The system sets initial placement; you don't control where windows/volumes appear. A window bar lets people reposition/resize.*

## Display 3D content in a person's surroundings (ImmersiveSpace)  ← KEY for placement
For control over placement, use **`ImmersiveSpace`** — an unbounded area where you control size and placement. With permission, use ARKit to integrate content into surroundings (e.g. scene reconstruction mesh).

```swift
@main struct MyImmersiveApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
        ImmersiveSpace(id: "solarSystem") { SolarSystemView() }
    }
}
```
- Default style `.mixed` shows your content WITH passthrough. Other styles hide passthrough; `.full` for total immersion. Set via `immersionStyle(selection:in:)`.
- **⚠️ In `.mixed`, too much content (even partially transparent) can hide real hazards.** Use `.full` if you want full immersion.
- **You must set positions** of items: SwiftUI views via modifiers, RealityKit entities via transform. **Origin starts at the person's feet** (can shift, e.g. SharePlay).
- Open with `openImmersiveSpace(id:)`; opening an ImmersiveSpace hides other apps. Dismiss the visible space before opening another.

---
### SPATAIL takeaways
- Our iOS app already uses RealityView + USDZ; this confirms the exact pattern.
- Mechanics = InputTargetComponent + CollisionComponent + targetedToAnyEntity gesture. This is the literal API our Mechanic Library wraps.
- Spatial UI panels = SwiftUI attachments placed relative to 3D (see combining-2d-and-3d).
- "Origin at the feet" + "you set positions" = our station-placement math lives here.
