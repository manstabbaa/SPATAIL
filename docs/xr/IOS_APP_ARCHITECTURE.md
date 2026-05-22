# SPATAIL Player iOS — Suggested Architecture

A reference architecture for the iOS AR app. **Build the app once; ship new explanations forever** — either as static `.spatail` bundles (offline) or via the live realtime session (online).

This document is opinionated and intentionally small. Hand it to a fresh Xcode project and you should be running in a day.

## Two modes, one renderer

| Mode | Source | When |
|---|---|---|
| **Offline** | `.spatail` from Files / AirDrop / URL | No network; user has bundles cached |
| **Live**    | WebSocket session to SPATAIL server | Default — user types a prompt, server runs Blender, bundle streams back |

Both modes feed the same `SceneController` + mechanic renderers. The difference is the loader:

- **Offline** → `BundleLoader.unzip(_:)` produces a folder URL.
- **Live** → `SessionClient` exposes the same folder URL after downloading USDZ via `asset.url` and writing a synthesised `manifest.json` from the live `experience.json`.

Everything downstream of the loader is mode-agnostic.

---

## Stack

- **Swift 5.9+**, **iOS 17+** (RealityKit 2 features + USDZ animation playback)
- **RealityKit** for the AR scene (NOT SceneKit — RealityKit handles USDZ + ARKit + ECS cleanly)
- **ARKit** for plane detection, world tracking
- **Combine** for sequence-step event streams
- **AVFoundation** for narration playback
- **Standard `URLSession` + `ZIPFoundation`** for bundle download / unzip

No third-party rendering. No Unity. No SceneKit fallback.

---

## Module layout

```
SpatailPlayer/
├── App/
│   └── SpatailPlayerApp.swift        ← @main, scene graph, URL handler
├── Bundle/
│   ├── BundleStore.swift             ← list/import/delete .spatail in app cache
│   ├── BundleLoader.swift            ← unzip, validate manifest, prefetch USDZ
│   └── Manifest.swift                ← Codable structs for manifest.json
├── Session/                          ← LIVE MODE
│   ├── SessionClient.swift           ← WebSocket client, event dispatch
│   ├── SessionEvent.swift            ← Codable for the realtime protocol
│   ├── RoomReporter.swift            ← ARKit plane → room.update deltas
│   ├── PoseReporter.swift            ← Throttled pose.update sender
│   ├── AssetDownloader.swift         ← Signed-URL fetch + disk cache
│   └── ExperiencePatcher.swift       ← Apply RFC 6902 patches to live contract
├── Contract/
│   ├── ExperienceContract.swift      ← Codable for experience.json (v0.5)
│   ├── Vocab.swift                   ← closed-enum mirrors of MECHANIC_KINDS etc.
│   └── PrimsIndex.swift              ← Codable for prims_index.json
├── Scene/
│   ├── SceneController.swift         ← owns the RealityKit Entity tree
│   ├── EntityRegistry.swift          ← prim-path ↔ Entity ↔ contract-id maps
│   └── AnchorPolicy.swift            ← chooses ARAnchor per anchorStrategy
├── Mechanics/
│   ├── MechanicRenderer.swift        ← protocol
│   ├── AnnotatedCalloutsRenderer.swift
│   ├── HighlightedRegionRenderer.swift
│   ├── ExplodedViewRenderer.swift
│   ├── CrossSectionRenderer.swift
│   ├── AssemblySequenceRenderer.swift
│   ├── TimelineRenderer.swift
│   ├── GhostedInternalRenderer.swift
│   └── PlaceholderRenderer.swift     ← fallback for unshipped mechanics
├── Animations/
│   ├── AnimationPrimitive.swift      ← protocol
│   ├── TransformKeyframes.swift      ← USDZ-baked tracks
│   ├── ExplodeAssemble.swift
│   ├── HighlightPulse.swift
│   ├── Fade.swift
│   ├── SetVisible.swift
│   └── AttentionCameraHint.swift
├── Interactions/
│   ├── TapDispatcher.swift           ← raycast → prim → contract action
│   ├── InteractionRegistry.swift     ← triggers / actions wired from contract
│   └── SequenceController.swift      ← runs sequences[], emits scene_event
├── UI/
│   ├── PickerView.swift              ← grid of installed bundles
│   ├── PlayerView.swift              ← ARView + chrome overlay
│   ├── ExplanationSheet.swift        ← shows explanation.written
│   └── ToastView.swift               ← step labels, errors
└── Resources/
    └── Info.plist                    ← UTI declaration for .spatail
```

---

## Hot path

```
.spatail picked
    │
    ▼
BundleLoader.unzip(_:) → unzipped folder URL
    │
    ▼
Manifest decode → schema version check
    │
    ▼
ExperienceContract decode → vocab validation
    │
    ▼
PrimsIndex decode
    │
    ▼
RealityKit Entity.load(contentsOf: scene.usdz) → rootEntity
    │
    ▼
EntityRegistry.bind(rootEntity, primsIndex)
    │
    ▼
Mechanics fanout — for each mechanics[] entry:
    let renderer = registry.renderer(for: mechanic.kind)
    renderer.attach(to: rootEntity, params: mechanic.params, registry: entityRegistry)
    │
    ▼
AnchorPolicy.anchor(rootEntity, strategy: contract.firstAnchorStrategy)
    │
    ▼
SequenceController.run(defaultSequenceId)
```

Once running, the AR session, the sequence controller, and the UI overlay all communicate via Combine publishers.

---

## Key types (sketch)

### Manifest

```swift
struct BundleManifest: Decodable {
    let schemaVersion: String
    let experienceId: String
    let title: String
    let createdAt: String
    let source: BundleSource
    let files: BundleFiles
    let scene: BundleScene
    let narrationLanguages: [String]
}

struct BundleScene: Decodable {
    let unitScale: Float
    let upAxis: String
    let boundingBoxMeters: [Float]      // [x, y, z]
    let defaultViewerDistanceMeters: Float
    let supportsRealScale: Bool
    let supportsTabletop: Bool
}
```

### Mechanic renderer protocol

```swift
protocol MechanicRenderer {
    static var kind: String { get }   // e.g. "annotated_callouts"
    func attach(
        to root: Entity,
        params: [String: Any],
        registry: EntityRegistry,
        sequence: SequenceController
    )
    func detach()
}
```

Mechanic renderers are registered at app launch:

```swift
let mechanicRegistry: [String: any MechanicRenderer.Type] = [
    "annotated_callouts": AnnotatedCalloutsRenderer.self,
    "highlighted_region": HighlightedRegionRenderer.self,
    "exploded_view":      ExplodedViewRenderer.self,
    "cross_section":      CrossSectionRenderer.self,
    "assembly_sequence":  AssemblySequenceRenderer.self,
    "timeline":           TimelineRenderer.self,
    "ghosted_internal":   GhostedInternalRenderer.self,
    // … unshipped mechanics route through PlaceholderRenderer
]
```

### Entity registry

```swift
final class EntityRegistry {
    private var byPrimPath: [String: Entity] = [:]
    private var byElementId: [String: Entity] = [:]
    private var byEntityId: [Entity.ID: String] = [:]   // for raycast hits

    func bind(_ root: Entity, primsIndex: PrimsIndex) { /* … */ }
    func entity(forElement id: String) -> Entity?
    func elementId(forEntity id: Entity.ID) -> String?
}
```

The registry is the single source of truth — every mechanic, animation, and interaction goes through it.

### Tap dispatch

```swift
// In ARView coordinator:
@objc func handleTap(_ gr: UITapGestureRecognizer) {
    let tap = gr.location(in: arView)
    guard let hit = arView.entity(at: tap),
          let elementId = entityRegistry.elementId(forEntity: hit.id)
    else { return }

    interactionRegistry.dispatch(trigger: .tap, on: elementId)
}
```

`InteractionRegistry.dispatch` walks the contract's `interactions[]`, finds entries whose `trigger.kind == .tap` and `target == elementId`, and executes their `actions[]`.

### Sequence controller

```swift
final class SequenceController: ObservableObject {
    @Published private(set) var currentStepId: String?
    @Published private(set) var stepIndex: Int = 0

    let sceneEvents = PassthroughSubject<String, Never>()  // emits "sequence_started" etc.

    func run(_ sequenceId: String) { /* … */ }
    func advance() { /* … */ }
    func previous() { /* … */ }
}
```

Mechanics subscribe to `sceneEvents` and react. Narration playback also subscribes — when `currentStepId` changes, play the matching `narration/<step_id>.m4a` if present.

---

## Anchoring

```swift
enum AnchorStrategy: String, Decodable {
    case worldAnchor = "world_anchor"
    case planeAnchor = "plane_anchor"
    case userRelative = "user_relative"
    case relativeToTarget = "relative_to_target"
    case simulatedAnchor = "simulated_anchor"
    // object_anchor falls back to worldAnchor in v1
}

struct AnchorPolicy {
    static func attach(_ entity: Entity, strategy: AnchorStrategy, in arView: ARView,
                       boundingBox: SIMD3<Float>, scaleMode: ScaleMode) -> AnchorEntity {
        switch strategy {
        case .worldAnchor, .simulatedAnchor, .objectAnchor:
            let anchor = AnchorEntity(world: spawnPoseAhead(of: arView, distance: 0.6))
            anchor.addChild(entity)
            return anchor

        case .planeAnchor:
            let anchor = AnchorEntity(plane: .horizontal,
                                       classification: .table,
                                       minimumBounds: [0.2, 0.2])
            anchor.addChild(entity)
            return anchor

        case .userRelative:
            // attach to the camera anchor; reposition each frame
            let anchor = AnchorEntity(.camera)
            entity.position = [0, 0, -0.6]
            anchor.addChild(entity)
            return anchor

        case .relativeToTarget:
            // placeholder: caller resolves the target entity and adds as child
            return AnchorEntity()
        }
    }
}
```

`scaleMode` is applied to the entity transform after anchoring.

---

## URL / file association

In `Info.plist`:

```xml
<key>UTImportedTypeDeclarations</key>
<array>
  <dict>
    <key>UTTypeIdentifier</key>            <string>com.spatail.experience</string>
    <key>UTTypeConformsTo</key>            <array><string>public.zip-archive</string></array>
    <key>UTTypeTagSpecification</key>
    <dict>
      <key>public.filename-extension</key> <array><string>spatail</string></array>
      <key>public.mime-type</key>          <array><string>application/x-spatail</string></array>
    </dict>
  </dict>
</array>

<key>CFBundleDocumentTypes</key>
<array>
  <dict>
    <key>CFBundleTypeName</key>            <string>SPATAIL Experience</string>
    <key>LSItemContentTypes</key>          <array><string>com.spatail.experience</string></array>
    <key>LSHandlerRank</key>               <string>Owner</string>
  </dict>
</array>
```

Then in `SpatailPlayerApp.swift`:

```swift
.onOpenURL { url in
    Task { await bundleStore.import(from: url) }
}
```

That's the full association: AirDrop a `.spatail`, tap "Open with SPATAIL Player," and the app receives it.

---

## Live-session hot path

```
User taps "Ask"
    │
    ▼
SessionClient.connect(wsURL, token)
    └── send session.start with capabilities
    ◄── receive session.ready
    │
    ▼
RoomReporter.start()  — push room.update deltas on first plane detection + on change
PoseReporter.start()  — push pose.update at 5 Hz
    │
User types / speaks prompt
    │
    ▼
SessionClient.send(user.prompt { text })
    │
    ◄── understanding.partial × N  (UI shows "classifying parts…")
    ◄── asset.url { sceneUsdz, heroThumbnail }
    │
    ▼
AssetDownloader.fetch(sceneUsdz)   — cache by etag/bundleId
    │
    ◄── experience.delta(full)
    │
    ▼
SceneController.load(usdz, contract)   — same code path as offline mode
    │
User walks around → ARKit emits new planes
    │
    ▼
RoomReporter.diff() → room.update(delta)
    ◄── experience.delta(patch)
    │
    ▼
ExperiencePatcher.apply(patches)   — RFC 6902 against the live contract
SceneController.replan(diff)        — only re-resolves placements, geometry untouched
```

The `Session/` module is the only place that knows about the wire format. Once `experience.delta` lands, every other module sees it as the same `ExperienceContract` value an offline bundle produces.

## v1 milestones

### Offline-first (start here)
1. **Static USDZ load + manifest parse** — open a bundle, see geometry, no interactions. (1 day.)
2. **Anchoring + scale modes** — bundle places correctly per contract. (1 day.)
3. **Tap → highlight** — `annotated_callouts` + `highlighted_region` mechanics. (2 days.)
4. **Sequences** — `SequenceController`, step-by-step playback with the `timeline` mechanic. (2 days.)
5. **`exploded_view` + `assembly_sequence`** — RealityKit transform animations on prim groups. (3 days.)
6. **Narration sync** — AVFoundation + step events. (1 day.)

### Live mode (after offline works)
7. **`SessionClient` MVP** — connect / send prompt / receive `asset.url` + `experience.delta(full)`. URLSession's `URLSessionWebSocketTask` is enough; no third-party. (2 days.)
8. **`RoomReporter`** — `ARWorldTrackingConfiguration.planeDetection = [.horizontal, .vertical]`, diff plane anchors, emit `room.update`. (2 days.)
9. **`ExperiencePatcher`** — apply RFC 6902 patches; trigger `SceneController.replan` selectively. (1 day.)
10. **Resync + reconnect** — handle WS drop with `session.resync`, retry with exponential backoff. (1 day.)

**Total v1 estimate: ~16 days.** A single developer. Offline mode is shippable at day 10.

---

## What to defer

- **Object anchoring on real objects** (ARKit object detection). Hardware-class dependent.
- **Room awareness.** ARKit `RoomCaptureSession` can supply this, but it's a separate flow — pair with the `roomContract` field in the contract.
- **Custom shaders.** `UsdPreviewSurface` is enough for v1. Save Metal shader work for `cross_section` clipping plane and `ghosted_internal` opacity ramps.
- **Audio narration generation.** Bundle ships pre-rendered audio; iOS just plays it.
