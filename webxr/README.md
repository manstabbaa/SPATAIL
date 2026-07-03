# SPATAIL · WebXR Content Viewer (PC dev surface)

The client/dev surface for SPATAIL per the 2026 Master File pivot
(`docs/spatail_2026_mf_pivot.md`): a visionOS-style spatial viewer built on
**Three.js + WebXR** that renders a SPATAIL scene contract + its GLB assets.

- **Runs flat-3D on a PC browser** — the dev tool.
- **Goes immersive on a headset browser** (Quest / Vision Pro / Android XR) via the
  `Enter XR` button — same code, no rebuild.
- Consumes the **same contract** the PC brain emits
  (`schemas/spatialExperienceContract.schema.json`).
- Keeps rendering when the tab/preview panel is backgrounded (rAF-pause fallback).

## Controls

| Button / key | What it does |
|---|---|
| `Identify / Hitboxes` · **I** | Per-element AABB hitbox (colored by intent) + label (declared size + anchor) + side panel of intent / representation / scale / affordances, with the planner's **"Why this / Why here"** on click. |
| `Controls` · **O** | **Object-local control panels** pinned to each object (MF p29/p35) — affordance buttons that drive real behaviors: **rotate**, **isolate** (dims the rest), **explode** (parts fly out from the centroid), **scale** (cycle), **animate** (plays a GLB clip if present), **reset**. |
| `Detections` · **L** | The sample 3D detection overlay (the detection data shape, offline). |
| `Live cam` · **C** | **Webcam → Gemini detector → boxes over the camera feed** (MF p28/p40). |
| `Story step` · **Space** | Walks the scene's attention track (MF p13), narrating each beat. |
| `✦ Generate` (prompt box) | **Live from the brain** — sends the prompt to `job_server /modular`, adapts the returned contract, and renders it (see below). |
| `◱ Staged setting` | The PC viewer is context-less, so `✦ Generate` POSTs `context: {"mode": "staged"}` by default — the brain composes a **setting** (see below). Toggle **off** to send `{"mode": "ar"}` for contract-only debugging (real-room context, no setting emitted). Persisted in `localStorage` (`spatail.stagedSetting`); default **on**. |
| `▲ Strict assets (fail like the phone)` | Renders **every GLB raw** — scale 1.0, no re-centre, no fit — so the PC reproduces phone failures instead of papering over them. Persisted in `localStorage` (`spatail.strictAssets`); default **off**. Toggling re-renders the current scene. |
| click an object | Selects it (raycast) and shows its object-local control panel. |
| `Enter XR` | Starts an immersive WebXR session on a headset browser. |

Assets **spawn in** with a voxel/particle materialization (MF p18/p19) as they load.

## Run it

```bash
# 1) serve the repo root (so /public GLBs and /webxr both resolve):
python -m http.server 8765
#    → http://localhost:8765/webxr/index.html

# 2) for the Live cam, run the perception server (Gemini key in ~/.spatail/secrets.env):
python webxr/live/detector_server.py            # listens on :8766
#    self-test without a browser:
python webxr/live/detector_server.py --self-test path/to/image.png
```

In Claude Code the registered preview servers are `spatail-webxr` (viewer) and
`spatail-detector` (perception) in `.claude/launch.json`. Point the viewer at a
different detector with `?detector=http://host:port`.

## Scene format

A scene is a thin projection of `schemas/spatialExperienceContract.schema.json`,
in `webxr/scenes/` and listed in `scenes/index.json`. Each `spatialElement`:

```jsonc
{
  "id": "engine_block",
  "title": "V8 engine block",
  "intent": "inspect",                 // explain|compare|simulate|guide|place|transform|recognize  (MF p41)
  "representationMode": "three_d_model",
  "scaleMode": "tabletop_scale",
  "anchorStrategy": "world_anchor",
  "asset": { "glbUrl": "/public/assets/spatail-library/mechanical/engine_block_simplified.glb" },
  "placement": { "kind": "table", "position": [0,1.0,0], "rotationDeg": [0,25,0], "sizeMeters": [0.5,0.42,0.5] },
  "affordances": ["rotate","explode","isolate","label","animate"],   // the interaction contract (MF p35)
  "whyThisRepresentation": "…",        // surfaced in the identify overlay (MF p12 "why it appears")
  "whyThisPlacement": "…"
}
```

> `placement.position` is the *resolved* preview transform. In production the
> contract carries placement **intent** (anchor/scale/relationship) and the
> Placement solver (`studio/spatail/placement_solver.py`) resolves it against the
> live RoomModel; the viewer just needs a transform to draw.

## Live video-intelligence (built)

`webxr/live/detector_server.py` is the live perception layer. The browser captures
a webcam frame, POSTs it to `/detect`, and Gemini (`gemini-2.5-flash`, reusing the
REST + strict-JSON pattern from `studio/director/vision.py`) returns labeled 2D
boxes. The viewer draws them over the camera feed; a coarse 3D projection is also
returned so detections can populate the spatial hitbox overlay. A confirmed
detection is a placement-recognition target (MF p28/p40) — the same overlay the
authored scene uses. Detector response shape:

```jsonc
{ "detections": [
  { "label": "intake manifold", "confidence": 0.93, "intent_hint": "explain",
    "source": "gemini:gemini-2.5-flash",
    "bbox": [x,y,w,h],                 // 2D, normalized 0..1 (over the video)
    "position": [x,y,z], "sizeMeters": [..] } ] }  // coarse 3D (assumed depth)
```

## How it maps to the Master File

| MF pillar / page | In the viewer |
|---|---|
| MATTER · Purpose Layer (p12) / intent (p41) | per-element `intent` + "why" in the identify overlay |
| MATTER · taxonomy (p15) | `experienceType` in the top bar; `representationMode` per element |
| MATTER · matter appearing / spawning in (p18/p19) | the spawn-in particle materialization on load |
| SPACE · placement (p26–29) | `anchorStrategy` / `placement.kind` shown on each hitbox |
| SPACE · recognition (p28) / Overlay type / RECOGNIZE (p40) | the **Live cam** Gemini detection overlay |
| IDENTITY · locus of control (p29) | object-local control panels pinned to objects |
| INTERACTION · affordances (p35) / behavior contract (p42) / adaptive runtime (p43) | `affordances` tags + the working **Controls** behaviors |
| INTERACTION · directors story (p13) | the **Story step** attention track |

## Live from the brain (built)

`✦ Generate` POSTs the prompt to `studio/server/job_server.py` `/modular` (the same
endpoint the phone uses) and renders the returned experience. The brain emits the
**v0.5 modular contract + v0.6 sceneContract**, where placement is *intent*
(anchor / layout / footprints), not coordinates. The viewer's adapter
(`brainToWebScene` in `viewer.js`) projects it into the web scene and **resolves
positions with a layout solver** (`arc` / `row` / `cluster` / `grid` / `stack`,
anchored to floor / table / wall) — the same job the on-device Placement solver does
against a real RoomModel. It maps:

- `understanding.intent` → element intent; `stage.layout`/`anchor` → layout + anchor strategy
- `sceneContract.placement.designSystem.interaction.semanticActions` → affordances
  (`isolate_part`→isolate, `highlight_part`→label)
- `assets[].glbUrl` (e.g. `/assets/spatail-library/astronomy/earth.glb`) → loaded
  cross-origin from the brain (`_send` already sets `Access-Control-Allow-Origin: *`)
- `sequence` → the Story attention track

A subject the LLM composer resolves to a library asset renders the **real GLB**
immediately (verified: "the planet earth" → `earth.glb`, 2,488 tris); a subject with
no model yet renders a **placeholder box** and the brain queues an asset build
(`generationJobId`) — poll `/jobs/{id}` and re-generate to pick up the finished GLB.

### Real-scale contract (baked assets honored)

`placeModel` branches on the per-asset real-scale contract that
`asset_service.produce` stamps into the modular contract
(`docs/xr/LIVE_BRAIN_SPEC.md`):

- `assets[].realScaleBaked` — the GLB is **already metric with an authored pivot**:
  rendered at **scale 1.0, never re-centred**, seated by mesh origin exactly like the
  iOS client (the layout solver gives baked assets the surface point, not a
  half-height-raised centre). A bad bake fails on the PC the same way it fails on
  the phone.
- `assets[].realSizeMeters` (no bake) — the longest dimension is fit to it. In
  normal mode the mesh is then re-centred as a preview courtesy; in strict mode the
  authored pivot is honored (fit, but no re-centre).
- neither — normal mode fits + re-centres to `placement.sizeMeters` (the old
  behavior); **strict mode renders it raw** (scale 1.0, no re-centre, no fit).

### Honest whys

The viewer **never presents client text as brain reasoning**. In the identify
panel: **`Brain:`** shows the brain's `decisionTrace` verbatim when the contract
carries it (spec §2.3) — `placement.decisionTrace` in the modular contract, or
`sceneContract.placement.designSystem.decisionTrace` when the projection is
present (the same dict, embedded) — and **`Web solver:`** shows
this client's own resolution (e.g. `arc layout, baseY=0.78, slot 2/4`). Authored
scenes keep their contract-authored `whyThisPlacement` under `Why here:`; the
fabricated placement prose for live scenes is gone.

### Staged setting (the setting law)

**The setting law (canon):** placement is computed against **context** — the real
room when there is one (AR), a **brain-composed setting** when there isn't
(staged). `POST /modular` gains an optional `"context": {"mode": "staged" | "ar"}`;
`"ar"` (or absent) = real-room context, no setting emitted; `"staged"` = the brain
composes the setting. The PC viewer has no real room, so `✦ Generate` sends
`"staged"` by default (the `◱ Staged setting` toggle turns it off for
contract-only debugging).

The contract gains an additive, optional `setting` block (metres, world space,
ground plane at y=0):

```jsonc
"setting": {
  "id": "garage-v8-001",
  "why": "<one sentence: why this setting fits the question>",
  "elements": [
    { "id": "car", "subject": "sedan car with its hood open",
      "assetPath": "/assets/… .glb OR null", "generationJobId": "<job id> OR null",
      "footprintMeters": [w, h, d],
      "pose": { "position": [x, y, z], "yawDeg": 0 } }        // position = SEAT POINT on the ground
  ],
  "experienceAnchor": { "elementId": "car", "name": "engine_bay",
                        "position": [x, y, z], "yawDeg": 0 },
  "ground": { "kind": "floor", "sizeMeters": [8, 8] },
  "ambiance": { "style": "3d-pastel", "light": "soft-day" }
}
```

How the viewer renders it (`renderSetting` in `viewer.js`):

- **Ground** — a subtle plane sized from `setting.ground`, with a soft edge line.
  The synthetic table hides while a setting is active (the setting IS the context);
  `ambiance.light: "soft-day"` lifts the hemisphere light a touch.
- **Elements** — each renders at its pose (`pose.position` is the seat point; the
  footprint box sits ON it, yawed by `yawDeg`). Elements with an `assetPath` load
  the GLB, fit its longest dimension to the footprint's longest, and seat it on the
  ground (setting elements are context dressing, not the asset-debug surface the
  strict toggle targets).
- **Pending elements** (`assetPath` null) render an **honest materializing field**:
  the viewer's voxel/particle spawn-in treatment, made persistent, inside the
  reserved footprint bounds, with a floating label
  **"materializing — generating on the brain"** — **never a fake solid stand-in**.
  The label's sub-line says whether a brain job exists (`generationJobId` null =
  generation unavailable, footprint reserved only).
- **Generation** — pending elements with a `generationJobId` poll `/jobs/{id}`
  through the same machinery as experience assets (several polls can run at once;
  loading a new scene cancels all of them) and **hot-swap** the field for the real
  GLB when it lands, re-sending the placement report.
- **Experience anchoring** — when the setting carries an `experienceAnchor`, the
  experience's layout origin moves to that anchor: for live `/modular` scenes the
  layout solver resolves **anchor-local** (baseY 0 — the anchor supplies the
  height) and the viewer maps positions to world at the anchor's position/yaw; for
  authored scenes, `spatialElements[].placement.position` is authored
  **anchor-local** and mapped the same way. The engine lands IN the engine bay,
  not on the abstract grid.
- **Identify** — the setting gets its own panel entry whose `Brain:` line shows
  `setting.why` verbatim (honesty rule: brain text is never blended with client
  text), and every setting element gets an entry tagged **setting element** with
  its footprint, pose, and job status.

**Demo scene:** `scenes/engine_in_car.json` (first in the picker, loads by
default) — the `v8_engine` experience wrapped in a garage setting: the car is
pending (footprint 4.6×1.5×1.8 m, no job — reserved footprint only), the tool cart
is pending, and the engine experience anchors at the `engine_bay` anchor at the
front of the car (y 0.9). The founder sees the materializing car field with the
correctly-anchored engine floating in the bay before the PC ever generates a car.

### Placement report (client:"web")

After the layout solver places a live `/modular` contract (and again after a Meshy
hot-swap or a strict-mode re-render, since the final placements change), the viewer
POSTs the spec §2.2 placement-report shape to `{brain}/placement-report` —
fire-and-forget; failures are console-logged, rendering never waits on it:

- `solverInputs` — anchor preference, scale mode, footprints with provenance
  (`footprintSource` passed through; `library`/`default_guess` inferred when the
  contract predates §2.3), and a `roomSummary` of the context the solver actually
  placed against: the synthetic room as built (table Ø1.5 m top at y 0.74, floor
  8 m) — or, for staged scenes, the brain-composed setting's ground (plus a
  `setting` echo of the id/anchor/ground), never both. The `fits` check runs
  against the setting ground when staged.
- `plan` — per element: resolved position, yaw, applied scale, a geometric `fits`
  check against the synthetic surfaces, and the `Web solver:` line as `reason`.
- `finalPlacements` — per element: the rendered `worldTransform` (16 floats,
  column-major), `renderScale`, and `corrections` describing exactly what the
  client did to the asset (`re-centred to AABB centre`, `strict: raw render`,
  `baked: rendered at scale 1.0…`, `meshy hot-swap`, …).

The report lands in `studio/out/traces/{experienceId}.json` → `reports[]` and shows
up in the brain's `GET /traces/view` attribution page next to the iOS client's.

Run the brain (or use the `spatail-server` preview config):
```bash
python studio/server/job_server.py --port 8788 --no-watchdog --no-keep-awake
```
Point the viewer at a different brain with `?brain=http://host:port`.
