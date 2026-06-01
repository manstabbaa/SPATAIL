# ARKit Framework
Source: https://developer.apple.com/documentation/arkit (via tutorials/data JSON)
Captured for SPATAIL — this is the room/scene understanding that feeds placement.

## Overview
AR adds 2D/3D elements to the live sensor view so they appear to inhabit the real
world. ARKit combines device motion tracking, world tracking, scene understanding,
and display conveniences. On visionOS, ARKit data flows through ARKitSession +
DataProviders.

## Topic Sections

### visionOS
- Setting up access to ARKit data — `/documentation/visionos/setting-up-access-to-arkit-data` — permission/privacy gate.
- **ARKitSession** — `/documentation/arkit/arkitsession` — main entry point for receiving ARKit data.
- **DataProvider** — `/documentation/arkit/dataprovider` — a source of live data from ARKit.
- **Anchor** — `/documentation/arkit/anchor` — identity, location, orientation of an object in world space.
- ARKit in visionOS — `/documentation/arkit/arkit-in-visionos` — immersive AR experiences.

Relevant providers (from visionOS tree): plane detection, scene reconstruction,
world tracking, image tracking, room tracking, hand tracking, object tracking.

### iOS
- Verifying Device Support and User Permission — `/documentation/arkit/verifying-device-support-and-user-permission`
- **ARSession** — `/documentation/arkit/arsession` — manages motion tracking, camera passthrough, image analysis.
- **ARAnchor** — `/documentation/arkit/aranchor` — position & orientation of an item in the physical environment.
- ARKit in iOS — `/documentation/arkit/arkit-in-ios` — **our current app is iOS ARKit**, important: visionOS uses a different session/provider model.

## Note for SPATAIL
Our shipping app is **iOS** (iPhone ARKit/RealityKit), so today we use ARSession/
ARAnchor + plane detection. The visionOS APIs (ARKitSession, DataProvider, room
tracking) are the forward path if/when we target Vision Pro. Placement logic
(RoomProfile/SpatailAnalysis) should stay abstracted so it maps to both.
