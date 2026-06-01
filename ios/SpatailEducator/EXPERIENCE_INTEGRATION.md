# Experience flow — iOS integration notes

How the post-compile XR education loop is wired in the app (Phases 1–3).

## Data flow
```
User: prompt + "Build a full lesson" toggle (ContentView .prompting)
  → SessionModel.generate()  [makeExperience == true]
  → GenerativeClient.generateExperience(prompt)
        POST /jobs {prompt, mode:"experience"}  → poll (10-min cap)
        → download experience.json → ExperienceSpec
        → download every station hero USDZ into a temp folder
  → SessionModel.experience = DownloadedExperience ; stage = .experiencing
  → experienceEpoch += 1
  → ARContainerView.updateUIView sees new epoch
        → Coordinator.present(exp, epoch)
            → ExperienceRuntime(view, usdzDir).present(spec)
                 StationLayout.poses(...)  → place hero + panels + mechanics per station
  Walk-through UI (ContentView .experiencing): prev/next → SessionModel.focusStation
        → model.onFocusStation(i) → runtime.focus(i)
  Tap in AR → Coordinator.onTap → runtime.handleTap(entity) → mechanic fires
  Per ARFrame → runtime.faceBillboards(camera)  (iOS 17 billboard fallback)
```

## Files
| file | role |
|---|---|
| `ExperienceSpec.swift` | Decodable spec (matches docs/experience_spec_contract.md) |
| `StationLayout.swift` | comfort-arc placement math (platform-neutral) |
| `ExperienceRuntime.swift` | @MainActor — places stations, panels, mechanics; focus |
| `PanelFactory.swift` | spec Panel → 3D card (ImageRenderer → UnlitMaterial) |
| `GenerativeClient.swift` | `generateExperience()` — submit/poll/download |
| `ContentView.swift` | `.experiencing` stage + prompt toggle + walk-through card |
| `ARContainerView.swift` | Coordinator hosts the runtime, taps, per-frame billboard |

## Compile-time watch list (verify in Xcode — couldn't compile on the Windows host)
1. **Concurrency:** `Coordinator` and `ExperienceRuntime` and `SessionModel` are all
   `@MainActor`. The two `ARSessionDelegate` `session(_:didUpdate:)` methods are
   `nonisolated` and hop to `@MainActor` via `Task`. If strict-concurrency flags an
   `@objc func onTap` on the `@MainActor` class, mark it `nonisolated` and keep the
   `Task { @MainActor in handleTap(at:) }` hop (already present).
2. **iOS 17 floor:** `BillboardComponent` and `TextureResource(image:options:)` are
   iOS 18+ — both are `#available(iOS 18, *)`-gated with iOS-17 fallbacks
   (`faceBillboards(toward:)`, `TextureResource.generate(from:)`). Deployment target
   is iOS 17.0.
3. **RealityKit APIs used:** `ModelEntity`, `PhysicsBodyComponent`,
   `PhysicsMaterialResource.generate`, `CollisionComponent`, `InputTargetComponent`,
   `generateCollisionShapes`, `MeshResource.generatePlane`, `UnlitMaterial`,
   `Entity.visualBounds`, `availableAnimations`/`playAnimation` — all iOS 17 valid.
4. **xcodegen:** new Sources files are picked up automatically (target `sources: [Sources]`).
   Run `xcodegen generate` before building.

## Manual test (on device)
1. PC spine up (Blender + job server); iPhone on Tailscale.
2. Scan room → "Generate something new…" → keep "Build a full lesson" ON →
   "teach me how a lever works" → Build lesson.
3. Expect ~3–6 min (Director authors one Blender hero per station). Stations appear
   on an arc on the table; walk-through card drives prev/next; tapping a hero fires
   its mechanic; quiz station shows options.
