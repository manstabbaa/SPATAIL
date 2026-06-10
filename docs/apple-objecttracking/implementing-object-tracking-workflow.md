# Implementing object tracking — the full workflow (Apple docs capture)

Source: developer.apple.com/documentation/visionOS/implementing-object-tracking-in-your-app
(captured 2026-06-10). Pipeline: **physical object → 3D model (USDZ) → Create ML training →
`.referenceobject` → app runtime**.

## System requirements (the hard constraints)
- Runtime: **iPhone iOS 27+** OR **Apple Vision Pro visionOS 2+**.
- Training: **Mac with Apple silicon, macOS 15+** (Create ML app or CLI). M2+ recommended.
- Reference objects trained with macOS 27/Xcode 27 toolchain require iOS 27 / visionOS 27+.
- visionOS simulator does NOT support object tracking; real device only.
- visionOS: object tracking works **only inside an ImmersiveSpace** (window/volume = silent failure).
  iOS has no such requirement (plain ARView session).

## Step 1 — object suitability
- Objects must be **rigid** (stable shape + appearance). Articulated/deformable items fail.
- Stationary objects → default low-frequency pose updates (low power).
- Moving/handheld objects → high-frame-rate tracking (visionOS: `highFrameRateTrackingEnabled`;
  iOS: put in `trackingObjects`). Pair with **extended training** for best results.

## Step 2 — obtain the 3D model (the "digital twin")
Two sanctioned routes:
1. **CAD/DCC authoring** — accurate geometry + PBR materials, exported USDZ. Best for
   glass/metal/shiny objects.
2. **Object Capture** (Reality Composer app on iPhone/iPad → photogrammetry → USDZ).
Quality bar: photorealistic appearance AND **exact real-world scale** — a scale mismatch makes
the augmentation float in front of/behind the real object. Create ML ignores animations,
cameras, lights inside the asset.
(SPATAIL note: a Meshy-generated mesh could be used here ONLY if photoreal + metrically
scaled — Object Capture is the safer training input; Meshy is for display content.)

## Step 3 — train in Create ML
GUI: Xcode → Open Developer Tool → Create ML → **Object Tracking template (Spatial)** →
drag USDZ into the 3D viewport → verify dimensions in the viewport corner.
- **Viewing angles**: All Angles (handheld, e.g. drill) / Upright (on a surface — disables
  bottom) / Front (e.g. coffee machine — disables bottom+rear). Restrict when possible = accuracy.
- **Training mode**: Standard (default) vs **Extended** (more data, bigger model, much longer
  training; needed for high-frame-rate/handheld quality).
- **Objects to avoid**: add USDZs of similar-looking items as negative examples.
- Multiple model sources per project → multi-object tracking.
- Training takes **hours** (Mac-spec dependent). This latency is a product-level constraint.

CLI (automatable, runs on a remote Mac):
```bash
xcrun createml objecttracker -s source.usdz -o tracker.referenceobject              # standard
xcrun createml objecttracker -s source.usdz -o tracker.referenceobject -m extended --all-angles
xcrun createml objecttracker -h                                                     # all options
```

## Step 4 — the `.referenceobject` artifact
- Container = trained ML model + (optionally) the source USDZ.
- Xcode build setting `REFERENCEOBJECT_STRIP_USDZ` strips the USDZ copy to shrink the file.
- At runtime `referenceObject.usdzFile` exposes the embedded USDZ (nil if stripped) — usable
  for wireframe preview/occlusion geometry.
- Loadable from ANY file URL at runtime (not just the app bundle) — visionOS sample uses a
  file importer; iOS `ARReferenceObject(archiveURL:)` takes any local URL. ⇒ **reference
  objects can be downloaded post-compile**, matching SPATAIL's experiences-as-data thesis.

## Step 5 — integrate
Four routes: Reality Composer Pro · RealityKit · ARKit-visionOS (`ObjectTrackingProvider`) ·
ARKit-iOS (`ARWorldTrackingConfiguration`). See the per-platform API files in this folder.
