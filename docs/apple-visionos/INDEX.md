# Apple visionOS / Spatial Computing — Captured Corpus
Captured for the SPATAIL spatial-education build. All pages fetched via Apple's
`tutorials/data` JSON API (HTML is JS-rendered and returns empty). Offline copies
live in this folder.

## What's here

### Design (HIG) — `hig/`
| file | source | why it matters |
|---|---|---|
| designing-for-visionos.md | HIG hub | immersion levels, comfort, eyes+hands, ergonomics |
| spatial-layout.md | HIG | **placement numbers**: FOV, depth, dynamic vs fixed scale, 60pt/16pt spacing |
| immersive-experiences.md | HIG | shared vs full space, mixed/progressive/full styles, **1.5 m boundary** |

### visionOS framework — `visionos/`
| file | why |
|---|---|
| _visionos-root.md | full topic tree (app construction, design, SwiftUI, RealityKit, ARKit, perf, migration) |
| adding-3d-content.md | RealityView, Model3D, volumes, **ImmersiveSpace**, interaction setup (code) |

### RealityKit — `realitykit/`
| file | why |
|---|---|
| _realitykit-root.md | full topic tree (ECS, materials, anchors, physics, game dev) |
| combining-2d-and-3d-attachments.md | **spatial UI panels** = RealityViewAttachment (code) |
| physics-and-gestures.md | the **live-mechanics** API surface (PhysicsBody, gestures, Billboard) |

### ARKit — `arkit/`
| file | why |
|---|---|
| _arkit-root.md | session/provider model; iOS vs visionOS difference |
| placing-content-on-detected-planes.md | **AnchorEntity(.plane(.table…))** + PlaneDetectionProvider (code) |

### Source index — `_sources.md`
The original URL list + fetching note.

## How this maps to the build plan
- **Spatial UI panels** (your "written → spatial UI") → `realitykit/combining-2d-and-3d-attachments.md`
- **Live mechanics / physics** (rigid/gooey, grab, tap) → `realitykit/physics-and-gestures.md`
- **Placement / comfort / scale** (fix "too small", arc the stations) → `hig/spatial-layout.md` + `hig/immersive-experiences.md`
- **Mixed-reality default** (your selling point) → `hig/immersive-experiences.md` (mixed immersion, 1.5 m bubble)
- **Anchoring to the real room** → `arkit/placing-content-on-detected-planes.md`

## Coverage notes
- Captured: the highest-value design + the exact APIs our Mechanic Library, spatial panels,
  and placement engine will wrap. Root trees give the full link map for deeper dives on demand.
- NOT exhaustively fetched (one-level breadth was prioritized to the build-relevant leaves):
  individual symbol reference pages (e.g. each PhysicsBodyComponent property), the SharePlay /
  video-playback / enterprise branches, and WWDC video transcripts. The root files list their
  paths so any can be pulled later if needed.
- WWDC video pages are JS apps with no clean JSON transcript endpoint — referenced by URL in
  designing-for-visionos.md (Principles of spatial design, Design great visionOS apps, Design
  interactive experiences) but not transcribed.
