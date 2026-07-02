# SPATAIL iOS Viewer

The **on-device (iOS) twin of `webxr/`** — a native ARKit + RealityKit app that
talks to the *same* PC brain and Gemini perception server the web viewer uses, and
mirrors its four capabilities on a phone/iPad in real AR:

| Web viewer (`webxr/`) | iOS Viewer |
|---|---|
| `✦ Generate` → `job_server /modular` | prompt bar → `ViewerNet.generateModular` (same endpoint) |
| Meshy generate + progress + hot-swap | `pollMeshy` → 9-stage progress → `swapModel` spawns the USDZ in |
| `⊞ Identify / Hitboxes` | projected 2D hitboxes + label + intent + affordances (`view.project`) |
| `⚙ Controls` (rotate/isolate/explode/scale) | tap an object → object-local control panel → `applyBehavior` |
| `◎ Live cam` → detector | `ARFrame` → JPEG → `detector_server /detect` → boxes |

iOS renders **USDZ** (RealityKit-native); the brain already serves a USDZ twin for
every Meshy/library asset (`/artifacts/{id}.usdz`, `/assets/.../*.usdz`), so the
viewer requests `asset.usdzUrl` and the generated job's `usdz_url`.

## Architecture

```
Sources/
  SpatailViewerApp / ViewerScreen.swift  — @main + SwiftUI HUD (prompt, toggles, hitbox/detection overlays, settings)
  ViewerModel.swift                       — ObservableObject: toggles, identified[], detections[], progress, command closures
  ViewerARView.swift                      — UIViewRepresentable + Coordinator: AR session, placement, identify projection,
                                            behaviours, live perception, Meshy poll + hot-swap
  ViewerNet.swift                         — /modular, /jobs polling, USDZ download, /detect  (self-contained networking)
  LayoutSolver.swift                      — placement INTENT (stage.layout + footprints) → local positions; affordances/intent
../Spatail/Sources/ModularContract.swift  — REUSED verbatim: the brain's v0.6 contract Codables
```

It reuses the proven RealityKit idioms from the frozen `ios/Spatail` app
(`Entity(contentsOf:)` with the iOS-18 availability split, `AnchorEntity(world:)`,
`visualBounds`, `OpacityComponent`, clip playback, real-scale fit/seat) but is a
clean, standalone target — it does **not** modify the frozen app.

## Build & run (needs a Mac with Xcode)

> This was authored on Windows; **iOS compiles only on macOS/Xcode**. On your Mac:

```bash
brew install xcodegen          # if needed
cd ios/SpatailViewer
xcodegen generate              # creates SpatailViewer.xcodeproj from project.yml
open SpatailViewer.xcodeproj   # select your device, Run (⌘R)
```

On first launch, open **Settings (gear)** and set:
- **PC brain**: `http://<your-pc-tailscale-host>:8788` (run `python studio/server/job_server.py --port 8788`)
- **Perception**: `http://<your-pc-tailscale-host>:8766` (run `python webxr/live/detector_server.py`)

Both phone and PC must be on the same Tailscale tailnet (or LAN). Then type a prompt,
tap **✦ Generate**, aim at a table/floor, and toggle **Identify / Controls / Live cam**.

## Notes / honest caveats

- **Live-cam box alignment** is approximate: the camera frame is oriented to portrait
  and sent to the detector; boxes are drawn over the full view (aspect-fill), so they
  can be slightly offset from a precise pixel match. A future pass can apply
  `ARFrame.displayTransform` for exact registration, or raycast each detection center
  into the world to drop a 3D hitbox (the same path the placement-recognition flow,
  MF p28/p40, will use).
- Placement uses a simple layout solver (arc/row/cluster) on a raycast surface. The
  frozen app's `PlacementSolver` + `RoomModel` can be dropped in for full
  design-system placement against a scanned room.
- visionOS: `ViewerARView` has a placeholder; the RealityView volumetric player is the
  Vision Pro build.
