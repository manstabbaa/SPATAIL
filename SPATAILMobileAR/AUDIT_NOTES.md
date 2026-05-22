# SPATAILMobileAR Audit — Pre-Build Verification

Authored on Windows; never compiled. The scaffold targets Xcode 16.3+ /
Swift 6.1 / iOS 16. Search the codebase for the literal tag
**`SPATAIL_NEEDS_MAC_BUILD_VERIFY`** to jump to each suspect line.

## Counts

| Category | Tagged lines |
|---|---|
| A. `PhysicallyBasedMaterial.Opacity` init variants | 4 |
| B. `MaterialParameters.Texture` positional init | 1 |
| C. `TextureResource.generate` sync vs iOS 18 async | 1 |
| D. `Entity` subclass MainActor isolation | 2 |
| E. `ARWorldTrackingConfiguration.sceneReconstruction` runtime guard | 1 |
| F. Trailing commas in argument lists (Swift 6.1 / Xcode 16.3+) | ~60 (no tag — uniform) |
| **Total tagged suspect lines** | **9** |
| Total Swift files walked | 33 |

## Categories — what to look for on first build

### A. `.transparent(opacity: .init(floatLiteral:))` / `.init(scale:)`

`Material.Blending.transparent(opacity:)` takes
`PhysicallyBasedMaterial.Opacity`, which conforms to
`ExpressibleByFloatLiteral`. Both
`.init(floatLiteral: 0.85)` and `.init(scale: 1.0)` should compile under
iOS 16/17 SDKs, and a plain `0.85` literal is the cleanest form. If
Xcode rejects either variant, replace with the float literal —
**no semantic change**.

Files: `HighlightMaterialFactory.swift`, `SpatialPanelBuilder.swift`,
`TabletopModelBuilder.swift`.

### B. `MaterialParameters.Texture(tex)` positional

`init(_ resource: TextureResource)` since iOS 15. Stable on 16/17.
Tagged once in `SpatialPanelBuilder.swift`; identical usage in
`TabletopModelBuilder.swift` line 102 is the same pattern.

### C. `TextureResource.generate(from:withName:options:)`

iOS 13+ sync API. iOS 18 added an `async` overload, so when the
deployment target moves up there will be two visible overloads. Force
the sync one by leaving the call site `try` (not `try await`). Tagged
in `PanelTextureRenderer.swift`.

### D. `Entity` subclasses + `@MainActor`

`HighlightableTargetEntity` and `ExplodableAssemblyEntity` subclass
`RealityKit.Entity`. RealityKit pushed `Entity` to MainActor isolation
in recent SDKs. Likely fine, but if Xcode complains about isolation,
mark the class declaration `@MainActor`.

### E. `ARWorldTrackingConfiguration.sceneReconstruction`

Setting `.mesh` on a non-LiDAR device crashes `session.run(...)`.
Guarded by `supportsSceneReconstruction(.mesh)`. This pattern is
correct and shipping in Apple's own samples — verify the guard line
exists and runs before `session.run`.

### F. Swift 6.1 trailing commas in arg lists

Pervasive across the scaffold (~60 occurrences). Compiles under
**Xcode 16.3+ only** (SE-0439). If the Mac is on Xcode 15, strip the
trailing commas — they're cosmetic. Easiest: regex-replace
`,(\s*\n\s*)\)` → `$1)` across the project.

## What's NOT tagged but you should sanity-check on first build

1. **SwiftUI iOS-16 APIs** — `NavigationStack`, `.navigationDestination(for:)`,
   `.toolbarBackground(_:for:)`. Deployment target is iOS 16.0 so these
   exist; if you bump the deployment target down, several views break.
2. **`ARView.cameraMode`** — `cameraMode: .ar` is the default and works
   on iOS 16/17. iOS 18 deprecated nothing here.
3. **`Material.Color(tint:texture:)`** — `init(tint: UIColor, texture: MaterialParameters.Texture?)`
   stable since iOS 15.
4. **`SimpleMaterial(color:roughness:isMetallic:)`** — the
   `roughness: Float` overload exists. iOS 17 prefers
   `roughness: MaterialScalarParameter` but Float still coerces.
5. **`MeshResource.generatePlane / generateBox / generateCylinder`** —
   stable across 13/15/16/17.

## What I cannot verify from Windows (will only show on real device)

- Whether the panel canvas → TextureResource path actually produces a
  readable texture on-device (the texture-options.semantic value is
  correct for color content; mip behaviour will need a glance).
- Whether tap-to-place's raycast hit at extreme angles produces a
  reasonable plane match on real surfaces.
- Whether scene-reconstruction mesh anchors arrive at the rate the
  RoomScannerService expects (delegate fires every few hundred ms in
  practice, but room geometry needs a 5-15s sweep to be useful).

## On first build — quick checks

1. Build for "iPhone 15 Pro" simulator first to surface compile errors
   fast. ARKit won't run there, but the type-check passes.
2. Set deployment target = iOS 16.0 in Build Settings if XcodeGen
   somehow lands a different value.
3. If you hit an opacity-type error, search-and-replace
   `.init(floatLiteral: ` and `.init(scale: ` with bare float literals
   in the four flagged sites.
4. On a physical device, expect to grant camera permission once;
   `NSCameraUsageDescription` is present in `Info.plist`.
