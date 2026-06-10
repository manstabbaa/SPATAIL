# SPATAIL — a spatial experience in your space

SPATAIL is the **XR layer**: you prompt it (text, photo, camera) and it answers with a
spatial experience rendered by your phone — either **overlaid on the real object** in
front of you, or **placed into your room**.

```
PROMPT (text / photo / camera)
   │
   ▼
SPATAIL BRAIN (PC, studio/server + studio/director)
   "how do we explain this spatially?" → picks the stream
   │
   ├── TRACKED stream — a real object is detected/known
   │     anchor = ARObjectAnchor (.referenceobject, iOS 27 object tracking)
   │     content mimics THE object (camera frames → Gemini multi-view → Meshy)
   │
   └── PLACED stream — nothing to track
         anchor + scale + interactions chosen by the
         SPATAIL Placement Design System (studio/spatail/design_system.py)
   │
   ▼
ASSET — MESHY is the 3D artist (studio/meshy: gemini_images → multi-image-to-3D → decimate)
   ▼
SCENE — BLENDER is the constructor/animator (imports Meshy GLB, splits parts,
        rigs, bakes named clips, materials, exports USDZ + manifest)
   ▼
PHONE — plays it (ios/SpatailEducator: clips / sequence / triggers contract;
        the artist bakes animations, the runtime plays them)
```

## Map

| Path | Role |
|------|------|
| `studio/server/` | Always-on job server (phone ↔ PC over Tailscale) + live/headless Blender drivers |
| `studio/director/` | The brain: understanding → experience contract (clips/sequence/triggers) |
| `studio/spatail/` | Placement Design System, room model, placement solver |
| `studio/meshy/` | Meshy asset pipeline (Gemini multi-view → multi-image-to-3D → decimate) |
| `asset_factory/` | Ingest factory: raw AI-generated models → normalized GLB/USDZ |
| `cowork-plugin/` | Cowork MCP plugin driving the asset factory (asset ops) |
| `public/assets/spatail-library/` | Served asset library (GLB/USDZ + manifests) |
| `ios/SpatailEducator/` | The phone app (iOS-first, dual-target iOS/visionOS) |
| `docs/apple-objecttracking/` | WWDC26 object-tracking corpus (iOS 27 / visionOS 27) |
| `docs/spatail-placement-design-system.md` | The Placement Design System spec |
| `docs/LEGACY.md` | What was retired in the 2026-06 pivot and what's parked |

## Run

- PC spine: `python studio/server/job_server.py` (Blender 5.1 live with MCP add-on on :9876).
- Phone: build `ios/SpatailEducator` on the Mac (`xcodegen generate` → Xcode → device);
  base URL points at the PC over Tailscale.
- Asset ops: drop raw models in `assets_raw/<group>/`, run
  `python asset_factory/worker_manager.py` (or use the Cowork plugin).

Tracked-mode reference objects are trained on the Mac with Create ML
(`xcrun createml objecttracker`) — see `docs/apple-objecttracking/`.
