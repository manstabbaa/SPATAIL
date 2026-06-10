# Apple Object Tracking — offline corpus (WWDC26 / iOS 27 / visionOS 27)

Captured 2026-06-10 from developer.apple.com (session 283 + linked documentation).
This corpus exists because **object tracking is now the core of SPATAIL's tracked-experience
stream**: the same `.referenceobject` file runs on iPhone (iOS 27, ARKit) and
Vision Pro (visionOS 2+/27, ObjectTrackingProvider) — train once, deploy to both.

| File | What it covers | Use when building |
|------|----------------|-------------------|
| `wwdc26-283-enhancements-object-tracking.md` | Session 283 summary, key announcements, slide code, timestamps | Understanding what changed this year |
| `implementing-object-tracking-workflow.md` | End-to-end workflow: 3D model → Create ML training (GUI + CLI) → `.referenceobject` → app integration | The asset/training pipeline (PC↔Mac orchestration) |
| `ios27-arkit-object-tracking.md` | iOS 27 API: `ARReferenceObject(archiveURL:)`, `detectionObjects` vs `trackingObjects`, `ARObjectAnchor` → `AnchorEntity`, constraints | The iPhone tracked-mode runtime |
| `visionos-objecttracking-api.md` | visionOS: `ObjectTrackingProvider`, `ReferenceObject.Configuration.highFrameRateTrackingEnabled`, `anchorUpdates`, metric-pose API, ImmersiveSpace requirement | The Vision Pro tracked-mode runtime (later target) |
| `spatail-implications.md` | What all of this means for SPATAIL's two-stream architecture, the training-latency constraint, and the tiered anchoring strategy | Product/architecture decisions |
| `transcript-wwdc26-283.txt` | Local whisper transcription of the session video (user's copy) | Reference |

## Primary sources
- Session: https://developer.apple.com/videos/play/wwdc2026/283/ ("Explore enhancements to visionOS object tracking")
- https://developer.apple.com/documentation/visionOS/implementing-object-tracking-in-your-app
- https://developer.apple.com/documentation/visionOS/exploring_object_tracking_with_arkit (sample code, visionOS 27 beta)
- https://developer.apple.com/documentation/arkit/objecttrackingprovider
- "Using a reference object with ARKit in iOS" (linked from implementing-object-tracking)
- Related: WWDC24 session 10101 (object tracking intro), WWDC25 session 289 (spatial accessories)

Fetch note: developer.apple.com HTML is JS-rendered/empty — use the JSON API mirror
`https://developer.apple.com/tutorials/data/documentation/<path>.json` (same trick as docs/apple-visionos/).
