# WWDC26 Session 283 — Explore enhancements to visionOS object tracking

~14 min session. Chapters: Intro 0:00 · Object tracking 2:20 · Spatial accessories 7:20
(creating 7:47, plug-and-play 11:48, in-app 12:22) · Next steps 13:03.
Local transcript: `transcript-wwdc26-283.txt`.

## The announcements that matter to SPATAIL

1. **Object tracking comes to iOS (iOS 27).** Reference objects are NOT platform-specific:
   the same `.referenceobject` trained in Create ML runs in iOS ARKit and visionOS apps,
   no retraining (≈6:00–6:20 in session). This is the "same system, different medium" point.

2. **Tracking objects in motion / handheld** (visionOS 27 + iOS 27):
   - New high-frame-rate tracking. visionOS: per-reference-object opt-in via
     `ReferenceObject.Configuration().highFrameRateTrackingEnabled = true` (a runtime
     setting, not a training setting — applies to any existing reference object).
   - iOS: high frame rate = put the object in `ARWorldTrackingConfiguration.trackingObjects`
     (vs `detectionObjects` for stationary, low-power).
   - Robust to partial hand occlusion (flashlight demo ~3:19).

3. **Extended training mode in Create ML** — higher accuracy/robustness, recommended with
   high-frame-rate tracking, takes significantly longer. Setting sits below viewing angles
   in the Create ML object tracking template; also on CLI. The CLI explicitly enables
   **running training on a remote machine** (~4:55) — i.e. an automatable Mac training node.

4. **Metric-space pose API** (visionOS 27, "coordinate-space correction"):
   `anchor.coordinateSpace(correction:)` — `.rendered` (display-corrected, for visually
   gluing content to the object) vs `.none` (true metric pose, for measuring). Medical-probe
   demo: measuring distance between vertebrae with a tracked handheld probe.

5. **Marker trick (~3:25):** if you can't get a photorealistic 3D model of an object, you can
   3D-print a known marker shape, train THAT as the reference object, and mount it on the
   item — instant tracking for arbitrary handheld objects.

6. **RealityKit "physical surroundings light"** mentioned in the flashlight demo — virtual
   light cast onto real surfaces (part of visionOS 27 RealityKit: physical space lighting,
   projective textures, cloth sim, Gaussian splats).

## Slide code (verbatim from session slides)

visionOS — enable high frame rate per reference object:
```swift
var configuration = ReferenceObject.Configuration()
configuration.highFrameRateTrackingEnabled = true
let refObjURL = Bundle.main.url(forResource: "flashlight", withExtension: ".referenceobject")
let refObject = try? await ReferenceObject(from: refObjURL!, configuration: configuration)
```

Create ML CLI (supports remote/automated training):
```bash
xcrun createml objecttracker -s source.usdz -o tracker.referenceobject -m extended --all-angles
```

iOS 27 — full object tracking pattern:
```swift
import ARKit
import RealityKit

class ObjectTrackingARSessionDelegate: NSObject, ARSessionDelegate {
    let arView = ARView(frame: .zero)
    var entities: [UUID: AnchorEntity] = [:]

    func start() throws {
        let stationaryObject = try ARReferenceObject(archiveURL:
            Bundle.main.url(forResource: "stationary", withExtension: "referenceobject")!)
        let movingObject = try ARReferenceObject(archiveURL:
            Bundle.main.url(forResource: "moving", withExtension: "referenceobject")!)

        let configuration = ARWorldTrackingConfiguration()
        configuration.detectionObjects = [stationaryObject]  // Low frame rate
        configuration.trackingObjects  = [movingObject]      // High frame rate

        arView.session.delegate = self
        arView.session.run(configuration)
    }
}
```
Delegate lifecycle: `didAdd` → wrap `ARObjectAnchor` in `AnchorEntity(anchor:)` and add to
`arView.scene`; `didUpdate` → latest poses / `isTracked`; `didRemove` → clean up.

## Spatial accessories (noted, NOT current SPATAIL scope)
visionOS 27 opens custom spatial accessories (LED constellation + IMU + Bluetooth; tracked
at display rate, low latency, occlusion/low-light robust; buttons/haptics). APIs:
`GCSpatialAccessory` (GameController) + `AccessoryTrackingProvider` (ARKit),
`.referenceaccessory` bundles, debug view in Settings (developer mode), DFRobot/MikroE
plug-and-play reference hardware. Relevant later if SPATAIL ever ships a physical anchor
puck; the 3D-printed-marker reference object is the software-only version of this idea.
