# visionOS Documentation Root
Source: https://developer.apple.com/documentation/visionos (via tutorials/data JSON)
Captured for SPATAIL spatial-education build.

## Overview
visionOS is the operating system that powers Apple Vision Pro. Create immersive
apps and games for spatial computing using familiar tools (SwiftUI, RealityKit,
ARKit) together with platform-specific spatial features.

## Topic Sections

### App construction
- Creating your first visionOS app — `/documentation/visionos/creating-your-first-visionos-app`
- Adding 3D content to your app — `/documentation/visionos/adding-3d-content-to-your-app` — depth & dimension; incorporate content into surroundings.
- Creating fully immersive experiences — `/documentation/visionos/creating-fully-immersive-experiences` — combine spaces with RealityKit/Metal content.
- Drawing sharp layer-based content — `/documentation/visionos/drawing-sharp-layer-based-content`
- Introductory visionOS samples — `/documentation/visionos/introductory-visionos-samples`
- Combining spatial support from multiple frameworks — `/documentation/visionos/combining-spatial-support-from-multiple-frameworks`
- Connecting iPadOS and visionOS apps over the local network — `/documentation/visionos/connecting-ipados-and-visionos-apps-over-the-local-network`

### Design
- Designing for visionOS — `/design/Human-Interface-Guidelines/designing-for-visionos` — design principles for an infinite 3D space.
- Adopting best practices for privacy and user preferences — `/documentation/visionos/adopting-best-practices-for-privacy`
- Improving accessibility support in your visionOS app — `/documentation/visionos/improving-accessibility-support-in-your-app`

### SwiftUI
- Canyon Crosser: Building a volumetric hike-planning app — `/documentation/visionos/canyon-crosser-building-a-volumetric-hike-planning-app`
- Hello World — `/documentation/visionos/world` — windows, volumes, immersive spaces (teach about Earth).
- Presenting windows and spaces — `/documentation/visionos/presenting-windows-and-spaces`
- Positioning and sizing windows — `/documentation/visionos/positioning-and-sizing-windows`
- Adopting best practices for persistent UI — `/documentation/visionos/adopting-best-practices-for-scene-restoration`

### RealityKit and Reality Composer Pro
- Reality Composer Pro — `/documentation/RealityComposerPro`
- Petite Asteroids: Building a volumetric visionOS game — `/documentation/visionos/petite-asteroids-building-a-volumetric-visionos-game`
- BOT-anist — `/documentation/visionos/bot-anist` — windows, volumes, animations.
- Swift Splash — `/documentation/visionos/swift-splash` — interactive ride.
- Diorama — `/documentation/visionos/diorama` — scene design in Reality Composer Pro.
- Building an immersive media viewing experience — `/documentation/visionos/building-an-immersive-media-viewing-experience`
- Enabling video reflections in an immersive environment — `/documentation/visionos/enabling-video-reflections-in-an-immersive-environment`
- Combining 2D and 3D views in an immersive app — `/documentation/RealityKit/combining-2d-and-3d-views-in-an-immersive-app` — attachments place 2D relative to 3D.
- Understanding the modular architecture of RealityKit — `/documentation/visionos/understanding-the-realitykit-modular-architecture`
- Using transforms to move, scale, and rotate entities — `/documentation/visionos/understanding-transforms`
- Capturing screenshots and video — `/documentation/visionos/capturing-screenshots-and-video-from-your-apple-vision-pro-for-2d-viewing`
- Implementing object tracking in your visionOS app — `/documentation/visionos/implementing-object-tracking-in-your-visionos-app`
- Placing entities using head and device transform — `/documentation/visionos/placing-entities-using-head-and-device-transform`
- Manipulating entities with solid collisions — `/documentation/visionos/manipulating-entities-with-solid-collisions`

### ARKit
- Happy Beam — `/documentation/visionos/happybeam` — Full Space game using ARKit.
- Setting up access to ARKit data — `/documentation/visionos/setting-up-access-to-arkit-data`
- Incorporating real-world surroundings in an immersive experience — `/documentation/visionos/incorporating-real-world-surroundings-in-an-immersive-experience` — content responds to local shape of the world.
- Placing content on detected planes — `/documentation/visionos/placing-content-on-detected-planes` — detect tables, floors, walls, doors.
- Tracking specific points in world space — `/documentation/visionos/tracking-points-in-world-space`
- Tracking preregistered images in 3D space — `/documentation/visionos/tracking-images-in-3d-space`
- Exploring object tracking with ARKit — `/documentation/visionos/exploring_object_tracking_with_arkit`
- Object tracking with Reality Composer Pro experiences — `/documentation/visionos/object-tracking-with-reality-composer-pro-experiences`
- Building local experiences with room tracking — `/documentation/visionos/building-local-experiences-with-room-tracking`
- Drawing in the air and on surfaces with a spatial stylus — `/documentation/visionos/drawing-in-the-air-and-on-surfaces-with-a-spatial-stylus`

### SharePlay
- Building a guessing game for visionOS — `/documentation/GroupActivities/building-a-guessing-game-for-visionos`
- Implementing SharePlay for immersive spaces in visionOS — `/documentation/visionos/implementing-shareplay-for-immersive-spaces-in-visionos`
- Configure your visionOS app for sharing with people nearby — `/documentation/GroupActivities/configure-your-app-for-sharing-with-people-nearby`
- Adding spatial Persona support to an activity — `/documentation/GroupActivities/adding-spatial-persona-support-to-an-activity`
- Synchronizing group gameplay with TabletopKit — `/documentation/TabletopKit/synchronizing-group-gameplay-with-tabletopkit`

### Video playback
- Destination Video — `/documentation/visionos/destination-video`
- Playing immersive media with RealityKit — `/documentation/visionos/playing-immersive-media-with-realitykit`
- Rendering stereoscopic video with RealityKit — `/documentation/RealityKit/rendering-stereoscopic-video-with-realitykit`
- (plus AVKit/AVFoundation playback configuration topics)

### Xcode and Simulator
- Running your app in Simulator or on a device — `/documentation/Xcode/running-your-app-in-simulator-or-on-a-device`
- Interacting with your app in the visionOS simulator — `/documentation/Xcode/interacting-with-your-app-in-the-visionos-simulator`

### Performance
- Creating a performance plan for your visionOS app — `/documentation/visionos/creating-a-performance-plan-for-visionos-app`
- Analyzing the performance of your visionOS app — `/documentation/visionos/analyzing-the-performance-of-your-visionos-app`
- Reducing the rendering cost of your UI on visionOS — `/documentation/visionos/reducing-the-rendering-cost-of-your-ui-on-visionos`
- Reducing the rendering cost of RealityKit content on visionOS — `/documentation/visionos/reducing-the-rendering-cost-of-realitykit-content-on-visionos`
- Understanding the visionOS render pipeline — `/documentation/visionos/understanding-the-visionos-render-pipeline`

### iOS migration and compatibility
- Determining whether to bring your app to visionOS — `/documentation/visionos/determining-whether-to-bring-your-app-to-visionos`
- Bringing your existing apps to visionOS — `/documentation/visionos/bringing-your-app-to-visionos`
- Bringing your ARKit app to visionOS — `/documentation/visionos/bringing-your-arkit-app-to-visionos`
- Making your existing app compatible with visionOS — `/documentation/visionos/making-your-app-compatible-with-visionos`

### Enterprise APIs for visionOS
- Accessing the main camera — `/documentation/visionos/accessing-the-main-camera`
- Building spatial experiences for business apps with enterprise APIs — `/documentation/visionos/building-spatial-experiences-for-business-apps-with-enterprise-apis`
- Locating and decoding barcodes in 3D space — `/documentation/visionos/locating-and-decoding-barcodes-in-3d-space`
