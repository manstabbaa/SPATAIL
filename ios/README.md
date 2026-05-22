# SpatailPlayer (iOS) — Macbook Bootstrap

This is the iOS side of SPATAIL. The Windows side is `pipeline/` + `skills/`. Both live in the same repo so the contract files (`docs/xr/`, `pipeline/spatail/experience_contract.js`) stay in sync.

**Read [`docs/xr/SYNC_WORKFLOW.md`](../docs/xr/SYNC_WORKFLOW.md) first.** It's the manual for keeping the two machines coherent.

---

## First 10 minutes on Macbook

Assumes Xcode 15+, Node 18+ (for codegen + sync checks).

```bash
# 1. Clone the repo (skip if you already have it)
git clone <your-remote-url> spatail
cd spatail

# 2. Install Node deps used by the sync tooling
npm install     # nothing required yet, but runs cleanly

# 3. Regenerate Swift from JS — proves the toolchain works on Mac
npm run sync:swift-vocab

# 4. Confirm the boundary files are coherent
npm run sync:check

# 5. Open the Swift package in Xcode
open ios/SpatailPlayer/Package.swift
```

At step 5, Xcode loads the `SpatailPlayer` library target. You can `Cmd-U` to run `VocabSyncTests` (host-side, no simulator needed) — they verify your local checkout's Vocab.swift matches the JS contract.

---

## Wiring an iOS app target around the library

The library doesn't ship an Xcode app project yet — by design, so the project file isn't a git diff trap. Create the app once:

1. **Xcode → File → New → Project → iOS → App.**
2. **Product Name:** `SpatailPlayer`. **Interface:** SwiftUI. **Language:** Swift. **Storage:** None.
3. Save it into `ios/SpatailPlayer-App/` (sibling to `ios/SpatailPlayer/`, gitignored — see `.gitignore` step below).
4. In the new project, **File → Add Package Dependencies → Add Local…** → pick `ios/SpatailPlayer/Package.swift`.
5. Replace the default `ContentView` with:
   ```swift
   import SpatailPlayer
   struct ContentView: View { var body: some View { PlayerView() } }
   ```
6. **Project → Info → Custom iOS Target Properties:**
   - Add `NSCameraUsageDescription`: "Used to anchor AR explanations to the real world."
   - Add `NSDocumentTypes` entry for `.spatail` (see `docs/xr/IOS_APP_ARCHITECTURE.md` §"URL / file association").
7. **Signing & Capabilities:** Team = your Apple ID, bundle id = `com.<you>.spatailplayer`.
8. **Run** on a real device with ARKit support.

The app target imports from the library. **All your real code goes into the library** (`ios/SpatailPlayer/Sources/...`) so it's git-tracked and reviewable; the app target is a 50-line shell that consumes it.

### `.gitignore` entry

Add at repo root (or extend the existing one):

```gitignore
# Xcode app target — generated, not tracked
ios/SpatailPlayer-App/

# Xcode local state
ios/**/xcuserdata/
ios/**/*.xcuserstate
ios/**/.DS_Store
ios/**/build/
```

---

## What's in the library right now

```
ios/SpatailPlayer/
├── Package.swift                       — SwiftPM manifest
├── Sources/SpatailPlayer/
│   ├── App/
│   │   └── SpatailPlayerApp.swift      — @main shell (use from app target or test in isolation)
│   ├── Bundle/
│   │   ├── BundleLoader.swift          — unzip + decode .spatail; TODO: ZIPFoundation
│   │   └── Manifest.swift              — Codable for manifest.json
│   ├── Contract/
│   │   ├── ExperienceContract.swift    — Codable for experience.json (v0.5)
│   │   ├── PrimsIndex.swift            — prim ↔ element-id maps
│   │   └── Vocab.swift                 — ⚠️ GENERATED. Do not hand-edit.
│   ├── Session/                        — LIVE MODE
│   │   ├── SessionEvent.swift          — wire types per REALTIME_PROTOCOL.md
│   │   └── SessionClient.swift         — actor wrapping URLSessionWebSocketTask
│   ├── Scene/
│   │   ├── SceneController.swift       — owns the RealityKit Entity tree
│   │   └── EntityRegistry.swift        — prim ↔ Entity lookup
│   ├── Mechanics/
│   │   └── MechanicRenderer.swift      — protocol; per-mechanic files land here
│   └── UI/
│       └── PlayerView.swift            — SwiftUI ARView wrapper
└── Tests/
    └── SpatailPlayerTests/
        └── VocabSyncTests.swift        — fails if codegen drifts
```

Sized for one developer: ~1.5k lines today, growing to ~5k as mechanics ship.

---

## Daily workflow

```bash
# Start of session:
git pull
npm run sync:check          # verifies my Swift code still matches the contract

# Work in Xcode. Build. Run on device.

# When committing:
git add -p
git commit -m "ios: <short description>"
# If you touched the contract or the protocol, prefix with `protocol:` instead
# of `ios:` so cross-boundary changes are easy to grep.
git push
```

**If `npm run sync:check` fails after a pull:** someone bumped the contract on Windows. Read the diff in `pipeline/spatail/experience_contract.js`, run `npm run sync:swift-vocab`, commit the new `Vocab.swift`. The failure message tells you exactly which file is out of date.

---

## Roadmap (offline-first, then live)

Listed in `docs/xr/IOS_APP_ARCHITECTURE.md` §"v1 milestones". The next 3 concrete steps:

1. **Wire ZIPFoundation** — replace the `unzipFailed` stub in `BundleLoader.swift`. Add the dep via SwiftPM. ~30 minutes.
2. **Load `bundles/f1_wheel_buttons.spatail`** (already exported on Windows; sync via git or AirDrop). Open the app, tap "Files," pick it. Should see the wheel float ahead of the camera. ~1 day.
3. **First mechanic: `annotatedCallouts`.** Create `Mechanics/AnnotatedCalloutsRenderer.swift`, register in `MechanicRegistry.shipped`, draw a `Text` entity above each tagged prim. ~2 days.

After offline mode renders cleanly, switch on the `Session/` module and connect to the dev server.

---

## Common gotchas

- **`unzipFailed`** — expected until you add ZIPFoundation. Until then, manually unzip a `.spatail` to a folder and pass the folder URL to `BundleLoader.load(from:)`.
- **`schemaUnsupported`** — the bundle was exported by a newer Windows-side pipeline than your `Manifest.swift` knows. Pull, run `npm run sync:swift-vocab`, rebuild.
- **`webSocketTask` immediately fails** — server isn't running on Windows. Start it: `python pipeline/server/spatail_session_server.py`.

---

## How "always in sync" actually works

Three mechanisms enforce coherence, listed in order of strength:

1. **Codegen** — `Vocab.swift` is regenerated from JS. Hand edits are caught by reviewers (the file starts with `⚠️ GENERATED`) and by the sync check.
2. **Schema version tokens** — `0.5.0-spatail` / `0.5.0-spatail-bundle` strings live in three places (JS, Python, Swift). `npm run sync:check` greps for them and fails on mismatch.
3. **Convention** — protocol-touching PRs are prefixed `protocol:`, must include `[ ] sync:check passed` in the description, and must touch both sides in the same commit.

If you find yourself wanting a fourth mechanism, add it to `tools/sync/` and document it in `docs/xr/SYNC_WORKFLOW.md`.
