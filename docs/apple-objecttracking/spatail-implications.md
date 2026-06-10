# What WWDC26 object tracking means for SPATAIL (analysis, 2026-06-10)

SPATAIL's thesis: put a spatial experience IN YOUR SPACE. Two delivery streams:

1. **TRACKED** — the experience is anchored ON a real object ("how does this trash can
   work?" → the explanation overlays the actual trash can).
2. **PLACED** — the experience is staged in free space ("show me an engine" → engine on
   the tabletop). This is what SPATAIL already does (world-anchored placement).

## The enabling fact
One `.referenceobject` (Create ML) now drives BOTH iPhone (iOS 27 ARKit) and Vision Pro
(visionOS 2+/27). iPhone-first development carries to Vision Pro for free — the dual-target
rule holds at the tracking layer too. Reference objects load from arbitrary file URLs, so
the PC job server can deliver them post-compile like any other asset. SPATAIL stays a
"post-compile XR content generator" — now for tracking targets too.

## The hard constraint nobody should design around silently
Reference-object training is **offline and slow**: Create ML on an Apple-silicon Mac, hours
per object (extended mode: much longer), and it needs a photoreal, metrically-scaled USDZ
digital twin as input. ⇒ "Walk up to an unknown object and get full 6-DoF tracked overlay
RIGHT NOW" is not physically possible with reference objects alone.

## Tiered anchoring strategy (the product answer)
- **Tier 1 — Prepared (full tracking):** a library of pre-trained `.referenceobject` files
  for objects worth preparing (the user's own things, demo catalog: engine, appliance,
  flashlight…). detectionObjects for stationary, trackingObjects for handheld.
- **Tier 2 — Instant (coarse anchor):** for unknown objects today: identify via vision
  model (photo → subject), then anchor the experience with LiDAR raycast + tap-to-pin
  world anchor near/over the object. The experience is object-TAILORED but world-anchored
  (no 6-DoF glue). This is the graceful fallback and ships first.
- **Tier 3 — Background upgrade:** every Tier-2 encounter can enqueue a training job:
  capture (Object Capture scan, or Gemini multi-view → Meshy twin if photoreal+scaled) →
  Mac training node runs `xcrun createml objecttracker` (Apple explicitly supports remote/
  CLI training) → `.referenceobject` lands in the library → next encounter is Tier 1.
- **Marker escape hatch (Apple-sanctioned):** train ONE reference object for a 3D-printed/
  standard marker; mount the marker on anything → instant true tracking for arbitrary
  objects. Good for demos and "track this now" power users.

## Pipeline implications
- The same capture (photos of the real object) feeds BOTH the display asset (Gemini
  multi-view → Meshy multi-image-to-3D → decimate) and, when photoreal+scale-true,
  potentially the training twin. Object Capture remains the gold-standard training input.
- Blender's role: scene construction ONLY — import Meshy GLB, split/rig per part, bake
  named clips, materials, export USDZ + manifest (asset_manifest v2 parts+clips contract
  already matches the "artist bakes animations, developer plays them" model).
- Experience contract needs an `anchoring` section:
  `{ mode: "object" | "world", referenceObject: <url>, objectId, fallback: "world" }`.
  iOS mounts the experience root on ARObjectAnchor (tracked) or on the existing raycast
  world anchor (placed). Everything above the root is identical.
- Mac becomes a first-class pipeline node (training daemon + Xcode builds); PC stays the
  always-on brain (job server + Blender + asset factory); phone stays the renderer.
