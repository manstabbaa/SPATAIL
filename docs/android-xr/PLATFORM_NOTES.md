# Android XR — platform notes (SPATAIL pivot, 2026)

Captured reference for the Android XR client. The repo previously had a full Apple
corpus (`docs/apple-objecttracking/`, `docs/apple-visionos/`) and **zero** Android XR
notes — this fills that gap. Treat specifics as **provisional / Dev-Preview-era** and
re-confirm against the live Android XR docs before relying on an exact API name.

> Status as of June 2026: **Jetpack XR SDK Developer Preview 4**; XR Runtime,
> Jetpack SceneCore, and ARCore for Jetpack XR perception are "moving to Beta soon."
> First device: **Samsung Galaxy XR** (Project Moohan), shipped; **Android XR Emulator
> is built into Android Studio** (Otter 2+), so no headset is required to start.

---

## 1. The stack

| Layer | Library | Role |
|---|---|---|
| Spatial UI (2D panels) | **Jetpack Compose for XR** (`SpatialPanel`, `Orbiter`, `SpatialGltfModel`) | declarative spatial UI + simple model embedding |
| 3D scene graph | **Jetpack SceneCore** (`Session`, `Entity`, `GltfModel`, `GltfModelNode`) | entities, transforms, glTF load, **animation**, materials |
| Perception | **ARCore for Jetpack XR** | plane detection, depth, hit-test, **anchors**, hand & eye tracking |
| Low-level (optional) | **OpenXR** | drop down only for raw input/runtime needs |
| Tooling | **Android Studio Otter 2+** + Android XR Emulator | build, run, debug without hardware |

SPATAIL's client is **Kotlin** on this stack. The heavy brain stays server-side
(see §6); the device runs the light loop (Kotlin engine core + placement + the
`SceneBackend` → `JetpackXrBackend`).

## 2. Coordinate frame / anchors

- Right-handed, **+Y up**, metres — matches the backend's GLB export (`bake_assets._export_glb`
  uses `export_yup=True`, metric). No axis conversion needed for GLB → SceneCore.
- World content should be **anchored**, not placed at fixed coordinates: create an
  ARCore for Jetpack XR **anchor** (from a plane hit-test or a trackable) and parent the
  entity to it, so it stays locked as the user moves. (Master File SPACE pillar:
  placement = "where the answer becomes meaningful," not raw coordinates.)

## 3. Loading models (GLB)

- **SceneCore path (preferred for interactive content):** `GltfModel.create(session, path)`
  loads a `.glb`; wrap in a `GltfModelNode` to set pose/material and to **run animation
  clips** — this is how slice-1 plays a baked clip on tap (maps the engine's `playClip`).
- **Compose path (quick embed):** `SpatialGltfModel` + `rememberSpatialGltfModelState` +
  `SpatialGltfModelSource.fromPath("models/x.glb")`.
- Assets come from the kept backend as GLB (the Meshy artist writes the `.glb` twin; the
  library bake is now GLB-canonical). Download to app storage, then load by path.

## 4. Perception (the RoomModel producer)

ARCore for Jetpack XR provides what the existing neutral `RoomModel` schema needs:
- **Planes:** configure `Session` with `PlaneTrackingMode.HORIZONTAL_AND_VERTICAL`;
  each plane gives pose, size, orientation → floor/table surfaces.
- **Hands:** `HandTrackingMode.BOTH` → per-hand joint poses (gesture source).
- **Depth + hit-test + anchors:** depth maps for scene mesh; hit-test a ray to a plane,
  then create an anchor at the hit.
- **Light estimate:** for matching virtual lighting.

Slice-1 uses only one horizontal plane → a stub RoomModel (one surface, no obstacles).
The full producer (depth mesh + semantic floor/table/wall classification + light + hands)
is a Phase 2 gap — ARCore semantic labeling is coarser than ARKit/RoomPlan, so obstacle
classification will start rougher.

## 5. Input → SPATAIL intent (the seam the Master File demands)

The file's defining idea: **gesture is local, intent is universal.** Native Android XR
inputs (hand pinch, gaze + pinch, controller ray/trigger, touch) must map to a SPATAIL
**intent** (`activate / select / move / scale / compare / isolate / explain …`); the
experience decides what that intent does. This abstraction does **not** exist in the code
yet (the iOS engine hardwires gestures to rules) — it's the #1 thing to build on Android.

Slice-1 implements exactly one mapping: **tap on entity → intent `activate` → playClip**.

## 6. Talking to the kept backend

The always-on job server (`studio/server/job_server.py`, port 8787, over Tailscale) is
reused unchanged. The client (OkHttp/coroutines) does:

```
POST /jobs   {prompt, client:{platform:"android"}}     -> {id, status}
GET  /jobs/{id}                                          -> {status, glb_url?, manifest_url?, ...}
GET  /artifacts/{id}.glb                                 -> GLB bytes
```

The server now shapes the wire contract by platform (`_client_platform`): **android
clients get `glb_url` + `manifest_url` as the primary model fields and never `usdz_url`**.
For a synchronous single object, `POST /modular` returns a contract whose assets already
carry `glbUrl`.

- **Cleartext:** the spine speaks plain HTTP over Tailscale; add a `network-security-config`
  exception for the spine's MagicDNS host (Android blocks cleartext by default).

## 7. Comfort profile

`studio/xr_design.py` now carries an **`android_xr`** device profile
(`xr_design.profile("android_xr")`). It inherits the human-factors constants (eye height
1.45 m, gaze-down 12°, 30° comfort cone, reach/read distances) and overrides only the
device/optics-specific values:

- `near_clip` **0.30 m** (video-passthrough MR, no see-through waveguide; provisional)
- `target_fps` **72** (Galaxy XR default), `max_fps` **90**

Marked **provisional** — validate on real hardware and update the profile.

### Galaxy XR (Project Moohan) — known specs
dual **3552×3840** micro-OLED · **72 Hz default / 90 Hz max** · Snapdragon **XR2+ Gen 2**
· 16 GB RAM · video passthrough · FOV not published (measure on device).

## 8. The SceneBackend → JetpackXrBackend map (port target)

The iOS engine core (`ios/SpatailEngine/.../Commands.swift`) is the clean seam: a closed
`BackendCommand` enum behind `SceneBackend.apply(_:)`. The Kotlin `JetpackXrBackend`
implements the same protocol:

| BackendCommand | Jetpack XR / SceneCore |
|---|---|
| `createVisual(asset)` | `GltfModel.create()` → `GltfModelNode` (or primitive) |
| `updateTransform` | set node pose |
| `playClip / stopClip` | `GltfModelNode` animation control |
| `applyEffect / clearEffect` | material mutation (baseColor/alpha/emissive) per the neutral **hotspot** schema (`studio/spatail/region_schema.py`) |
| `addPhysics / applyImpulse` | SceneCore collision / a physics lib |
| `anchor` | ARCore for Jetpack XR anchor (plane hit-test) |
| `raycast` | ARCore hit-test |
| `speak / playSound / hud` | TTS / audio / Compose XR panel |

## 9. Sources
- Android XR SDK Dev Preview 4 — https://android-developers.googleblog.com/2026/05/android-xr-sdk-developer-preview-4-updates.html
- ARCore for Jetpack XR — https://developer.android.com/jetpack/androidx/releases/xr-arcore
- Detect planes — https://developer.android.com/develop/xr/jetpack-xr-sdk/arcore/planes
- Work with hands — https://developer.android.com/develop/xr/jetpack-xr-sdk/arcore/hands
- Create anchors — https://developer.android.com/develop/xr/jetpack-xr-sdk/arcore/anchors
- Add 3D models — https://developer.android.com/develop/xr/jetpack-xr-sdk/add-3d-models
- XR SceneCore — https://developer.android.com/jetpack/androidx/releases/xr-scenecore
- Create an Android XR project — https://developer.android.com/develop/xr/jetpack-xr-sdk/create-project
- Galaxy XR specs — https://roadtovr.com/samsung-galaxy-xr-headset-price-specs-release-date/
