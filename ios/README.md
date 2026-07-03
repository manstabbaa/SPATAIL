# ios/ — the Spatail client

One app, one package. Everything else that used to live here (SPATAILMobileAR,
SpatailViewer, SpatailPlayer, `_legacy_Spatail`) was retired in the 2026-07-03
teardown — see `docs/LEGACY.md`; recover from git history if ever needed.

```
ios/
├── Spatail/        — the product app (iPhone/iPad, ARKit). xcodegen project.
│   ├── project.yml — source of truth; Spatail.xcodeproj is generated from it
│   └── Sources/    — App/Core/Contracts/DesignSystem/Lens/Net/Perception/
│                     Placement/Registry/Room/Runtime/Settings — one target
└── SpatailEngine/  — platform-agnostic spatial-experience engine (SwiftPM).
                      Pure simulation core + RealityKit adapter seam.
                      Consumed by the app as a local package.
```

**Spatail** is the Lens client for the PC Live Brain: it streams the room up,
receives contracts (`Sources/Contracts/` — raw-string tolerant decoding), and
runs experiences via `SpatailEngine`. Two streams: object-TRACKED overlay
(iOS 27 object tracking, gated behind `SPATAIL_IOS27_TRACKING` in `project.yml`)
and world-PLACED (design-system placement).

## Read first

- `CLAUDE.md` (repo root) — build mentality + project orientation.
- `docs/spatail_2026_mf_pivot.md` — the 2026 Master File (the product spec).
- `docs/xr/LIVE_BRAIN_SPEC.md` — the PC brain the app talks to.
- `docs/spatail_engine_spec.md` — the engine's design spec.
- `ios/SpatailEngine/README.md` — engine architecture + seam.

## Build (Mac)

`xcode-select` points at the CLT on this Mac, so prefix builds with
`DEVELOPER_DIR`.

```bash
# App — regenerate the project, then build (no signing needed for a checkbuild)
cd ios/Spatail
xcodegen generate
DEVELOPER_DIR=/Applications/Xcode.app xcodebuild \
  -project Spatail.xcodeproj -scheme Spatail \
  -destination 'generic/platform=iOS' -configuration Debug \
  CODE_SIGNING_ALLOWED=NO build

# Engine — pure-core tests run host-side, no simulator
cd ios/SpatailEngine
DEVELOPER_DIR=/Applications/Xcode.app swift test
```

## Deploy to a device

Build signed (add your team via Xcode signing or
`DEVELOPMENT_TEAM=<TEAMID> CODE_SIGN_STYLE=Automatic` on the xcodebuild line,
with `-destination 'platform=iOS,id=<device-udid>'`), then install + launch
with devicectl — this is the recipe that is verified working against an
iOS 27 iPhone:

```bash
xcrun devicectl list devices                       # find the device id
xcrun devicectl device install app \
  --device <device-udid> <path/to/Spatail.app>     # from DerivedData/Build/Products
xcrun devicectl device process launch \
  --device <device-udid> dev.spatail.Spatail
```

## Conventions

- `Spatail.xcodeproj` is generated — edit `project.yml`, then `xcodegen generate`.
- Commit prefix `ios:` (or `protocol:` when a change touches the wire contract
  on both the PC and iOS sides in the same commit).
- Contract vocabularies are decoded as raw Strings on purpose (unknown values
  must never break decoding); the vocabulary source of truth stays
  `pipeline/spatail/experience_contract.js` + `docs/xr/`.
