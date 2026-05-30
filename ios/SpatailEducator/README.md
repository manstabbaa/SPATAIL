# SPATAIL Educator — iOS / Vision Pro player

The device end of the engine. It does **not** model anything — it loads the USDZ
the studio built, reads the SPATAIL metadata, scans your room, runs SPATAIL
ANALYSIS to pick a scale that fits, and plays the demo in AR.

```
Blender → SPATAIL (USDZ + metadata) → THIS APP (ARKit + RealityKit)
```

## What it does on device
1. **Scan** — detects floor + a desk-height surface → builds a `RoomProfile`.
2. **Ask** — "what do you want to see?" lists the bundled demos.
3. **Analyze** — `SpatailAnalysis` (Swift port of `studio/spatail/analysis.py`,
   identical rules) proposes **tabletop** vs **real-scale** variants for *your*
   space and recommends one.
4. **Place** — anchors the chosen exhibit's USDZ and plays its baked animation.

## Build on the Mac (one-time tools)
```bash
# tools
brew install xcodegen        # generates the .xcodeproj from project.yml
# get the code
git clone https://github.com/manstabbaa/SPATAIL.git   # or: git pull
cd SPATAIL && git checkout studio-pivot

# generate + open
cd ios/SpatailEducator
xcodegen generate
open SpatailEducator.xcodeproj
```
In Xcode: select your iPhone as the run destination, set your Team under
Signing & Capabilities (personal Apple ID is fine), and press ⌘R. Approve the
camera prompt, scan your space, pick a law, choose tabletop or real scale.

> AR needs a real device (ARKit doesn't run in the Simulator). For Vision Pro,
> select the visionOS destination — the scan/analysis/flow are shared; the
> volumetric RealityView player is the next increment.

## Regenerating assets (on Windows, with Blender)
```bash
python studio/ios_sync.py          # builds every beat → USDZ + metadata → Resources/
```
Then re-run `xcodegen generate` if file names changed, and rebuild.

## Bundled today
`studio_law1_inertia`, `studio_law2_fma`, `studio_law3_action_reaction`
(Newton's three laws) — each an AR-ready, animated USDZ + its SPATAIL metadata.
