# RealityKit Framework
Source: https://developer.apple.com/documentation/realitykit (via tutorials/data JSON)
Captured for SPATAIL — this is the runtime that plays our USDZ + drives mechanics.

## Overview
RealityKit is an AR-first, high-performance 3D simulation/rendering framework for
iOS, iPadOS, macOS, tvOS, visionOS. It leverages ARKit to integrate virtual
objects into the real world. Entity-Component-System (ECS) architecture.

## Topic Sections

### Essentials
- Understanding the modular architecture of RealityKit — `/documentation/visionos/understanding-the-realitykit-modular-architecture`
- Building an immersive experience with RealityKit — `/documentation/realitykit/building-an-immersive-experience-with-realitykit` — systems + postprocessing.
- **Entity** — `/documentation/realitykit/entity` — a scene element you attach components to for appearance & behavior.
- **Component** — `/documentation/realitykit/component` — geometry or behavior applied to an entity.

### Presentation
- Views and attachments — `/documentation/realitykit/presentation-views-and-attachments` — bring RealityKit content into the app.
- Presentation UI — `/documentation/realitykit/presentation-user-interface`

### Scene management and logic (ECS)
- Scenes — `/documentation/realitykit/ecs-scenes` — context holding all entities.
- Systems — `/documentation/realitykit/ecs-systems` — apply behaviors & physical effects per-frame.
- Events — `/documentation/realitykit/ecs-events` — subscribe to scene events.
- Entity actions — `/documentation/realitykit/ecs-entity-actions` — reusable actions that animate/change state.

### Asset creation
- Reality Composer Pro — `/documentation/RealityComposerPro`
- Presenting an artist's scene — `/documentation/realitykit/presenting-an-artists-scene`
- Object capture — `/documentation/realitykit/realitykit-object-capture` — photogrammetry.
- USD — `/documentation/USD` — scene representation (our USDZ export target).
- Composing interactive 3D content with RealityKit and Reality Composer Pro — `/documentation/realitykit/composing-interactive-3d-content-with-realitykit-and-reality-composer-pro` — animation timeline.

### Scene content
- Creating a spatial drawing app with RealityKit — `/documentation/realitykit/creating-a-spatial-drawing-app-with-realitykit`
- Generating interactive geometry with RealityKit — `/documentation/realitykit/generating-interactive-geometry-with-realitykit`
- Combining 2D and 3D views in an immersive app — `/documentation/realitykit/combining-2d-and-3d-views-in-an-immersive-app` — **attachments place 2D UI relative to 3D** (key for spatial panels).
- Transforming RealityKit entities using gestures — `/documentation/realitykit/transforming-realitykit-entities-with-gestures` — standard visionOS gestures on any entity.
- Responding to gestures on an entity — `/documentation/realitykit/responding-to-gestures-on-an-entity` — input target + collision components (key for Grab/Tap mechanics).
- Models and meshes — `/documentation/realitykit/scene-content-models-and-meshes`
- Materials, textures, and shaders — `/documentation/realitykit/scene-content-materials-and-shaders`
- **Anchors** — `/documentation/realitykit/scene-content-anchors` — lock virtual content to the real world (key for placement).
- Lights and cameras — `/documentation/realitykit/scene-content-lights-and-cameras`
- Content synchronization — `/documentation/realitykit/scene-content-content-synchronization`
- Audio — `/documentation/realitykit/scene-content-audio` — spatial audio.
- Videos — `/documentation/realitykit/scene-content-videos`
- Images — `/documentation/realitykit/scene-content-images`

### Game development (key for our "live mechanics")
- Gaming sample code projects — `/documentation/realitykit/game-development-sample-code`
- Entity animations — `/documentation/realitykit/game-development-entity-animations` — move/rotate/scale at runtime.
- Character control, skeletons, and inverse kinematics — `/documentation/realitykit/game-development-character-skeletons`

### Physics simulation (key for "slap physics on objects")
- Collision detection — `/documentation/realitykit/physics-collision-detection`
- Simulations and motion — `/documentation/realitykit/physics-simulations-and-motion`
- Force effects — `/documentation/realitykit/physics-force-effects`
- Physics joints and pins — `/documentation/realitykit/physics-joints-and-pins`

### Performance
- Improving the Performance of a RealityKit App — `/documentation/realitykit/improving-the-performance-of-a-realitykit-app`
- Reducing GPU Utilization — `/documentation/realitykit/reducing-gpu-utilization-in-your-realitykit-app`
- Reducing CPU Utilization — `/documentation/realitykit/reducing-cpu-utilization-in-your-realitykit-app`
- Construct an immersive environment for visionOS — `/documentation/realitykit/construct-an-immersive-environment-for-visionos`
