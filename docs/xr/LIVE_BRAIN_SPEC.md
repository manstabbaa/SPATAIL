# SPATAIL Live Brain — wire & fusion spec v1 (2026-07-02)

> Companion to `REALTIME_PROTOCOL.md` §10 (TRACKED stream). **Normative for the 2026-07
> rebuild**: the Spatail iOS app and the PC brain implement this exactly. Existing §10
> messages keep their current field names — everything here is **additive** unless marked
> otherwise. Where this spec and older docs conflict, this spec wins.

## 0. Laws (non-negotiable)

1. **Latency split**: ARKit owns WHERE and FORM at 60 Hz; the VLM owns WHAT at ~1 Hz.
   Identity may be ~1 s stale; geometry never is. Between identity ticks, every hitbox and
   placed experience rides its ARAnchor untouched.
2. **Nothing heavy on the phone's main thread**: camera JPEG encode on a background queue;
   Vision/CoreML inference on a dedicated serial background queue with a single-flight
   latch (latched *before* dispatch); LiDAR mesh extraction on a background actor
   publishing immutable snapshots; RealityKit/scene mutation main-only and **diff-only**
   (never `removeAll()` + rebuild).
3. **Backpressure**: max **1 frame in flight** on the uplink, drop-on-busy (latest-wins);
   no unbounded queues anywhere; N (default 5) consecutive send failures flips connection
   state to `.failed` — the UI never lies about streaming.
4. **Replans are cheap for the phone**: the server gates replans (§1.4) and the phone
   applies `experience.delta` by element-id diffing, so label flapping can never thrash
   the scene.
5. **Verification-first**: if you can't see what it saw, it doesn't ship.

## 1. Vision socket additions (`ws://<pc>:8798/v1/vision`)

Transport unchanged: binary frames = JPEG camera frames; text frames = JSON control
messages. All new messages are JSON text.

### 1.1 `pose.update` (phone → PC, ≤ 2 Hz)

Keeps the brain's gaze ray live between manual room sends. Never per-frame.

```json
{"type": "pose.update", "payload": {
  "position": [x, y, z],
  "forward":  [x, y, z],
  "up":       [x, y, z],
  "timestamp": 1783036800.123
}}
```

Server: `_handle_control` updates `self._pose`; **does not** trigger a replan.

### 1.2 `room.update` payload gains `objects[]`

```json
"objects": [{
  "id": "8C1A…-uuid",
  "label": "water bottle",          // null until identity attached
  "confidence": 0.91,               // label confidence; 0 when label null
  "obb": {"center": [x,y,z], "extents": [ex,ey,ez], "yaw": 0.42},
  "supportSurfaceId": "surf-…",     // null if unknown
  "lastSeenAt": 1783036800.123
}]
```

Coordinates: metres, world space, same convention as existing `surfaces`. `yaw` is
rotation about gravity-aligned +Y. Existing `surfaces` fields unchanged; surfaces MAY
additionally carry `"confidence"` (0–1, classification confidence) and a concave
`"boundary"` polygon (list of [x,y,z]) — additive, optional.

The payload MAY also carry `"concept": "<the user's ask>"` (optional string). It scopes
live replans to a question — the fusion brain only emits a part-addressed
`target: {objectId, part}` when the concept names the part — so the phone's Ask flow
sends it when the user asks about something in view. Absent → the engine clears any
held concept (stale questions must not steer later plans).

### 1.3 `vision.identification` additions (PC → phone)

Existing fields (primary, detections[{label, confidence, box}], rawText, latencyMs,
model) unchanged — there is no top-level `confidence`; the primary's confidence is
`detections[0].confidence`. Added:

```json
"frameTimestamp": 1783036800.123,   // capture time of the frame this identity describes
"parts": [{"label": "cap", "box": [x, y, w, h], "confidence": 0.8}]
```

`parts` describes sub-parts of the **primary** object; `box` is normalized [0–1] in the
same image space as `detections[].box`. The VLM prompt is extended to request parts for
the primary object; parts may be absent.

### 1.4 Replan gating (PC, replaces raw-string trigger)

Replan fires only when ALL hold:
- **binding change**: the mapped surface kind changed (`labelToSurfaceKind`) OR the
  bound object id changed (§2 of fusion, objects-first) — raw label string changes with
  the same binding do NOT replan;
- **confidence** ≥ `SPATAIL_REPLAN_MIN_CONF` (default 0.55);
- **dwell**: the same binding held for `SPATAIL_REPLAN_DWELL` (default 2) consecutive
  identifications.

Replans run as a **detached asyncio task** (the existing `_plan_lock` serializes them);
the identification downlink never blocks on the brain. VLM timeout default drops to 8 s.

### 1.5 Delta gate (phone)

The phone ignores `experience.delta` until it has sent a `room.update` on the *current*
connection (the rule §10 documents; now actually implemented).

## 2. Job server additions (`http://<pc>:8787|8788`)

### 2.1 Placement traces

- Every `/modular` response is persisted **before** returning:
  `studio/out/traces/{experienceId}.json` →
  `{"experienceId", "createdAt", "prompt", "contract", "sceneContract"?, "schemaValidation": {"ok", "errors": []}, "reports": []}`
- Every fusion replan appends `studio/out/traces/vision/{session}/plan_{NNNN}.json` →
  `{"planVersion", "trigger", "brainInput", "plan"}` — `brainInput` is byte-identical to
  what `plan_from_room.js --stdin` accepts, so any archived plan replays offline.

### 2.2 Endpoints

- `GET /traces` → JSON list `[{experienceId, createdAt, title, reportCount}]`
- `GET /traces/{experienceId}` → the trace file
- `POST /placement-report` → appends into `trace.reports[]`; body:

```json
{
  "experienceId": "…",
  "client": "ios" | "web",
  "reportedAt": 1783036800.123,
  "solverInputs": {"anchorPreference": "table", "scaleMode": "…", "coverage": 0.8,
                   "footprints": [{"assetId": "…", "meters": [x,y,z], "source": "library|object_size_llm|default_guess"}],
                   "roomSummary": {"surfaces": [{"kind": "table", "sizeMeters": [w,d], "y": 0.74}]}},
  "plan": {"anchor": "…", "placements": [{"elementId": "…", "position": [x,y,z], "yaw": 0.0,
            "scale": 1.0, "fits": true, "reason": "…"}]},
  "finalPlacements": [{"elementId": "…", "worldTransform": [16 floats, column-major],
                       "renderScale": 1.0, "corrections": ["table-pin -0.02m"]}]
}
```

- `GET /traces/view` → static HTML attribution page: one row per experience, four columns
  **Brain** (decisionTrace + whys) · **Contract** (validated JSON summary, footprint
  provenance) · **Client** (intent vs reported deltas, highlighted when they disagree) ·
  **Asset** (QA/verify renders, placementClass, xrReady).

### 2.3 Decision provenance (inside contracts)

- `decisionTrace: [string]` — which hint/threshold fired at each step (emitted by
  `studio/spatail/design_system.py`). Path: `placement.decisionTrace` in the **modular**
  contract; `placement.designSystem.decisionTrace` in the **sceneContract** projection
  (which embeds the whole modular placement as `designSystem`).
- Per-asset `footprintSource: "library" | "object_size_llm" | "default_guess"`.
- `schemaValidation` stamped into the trace (warn-only; never blocks the response).

## 3. ObjectRegistry fusion semantics (on device)

The registry is the meeting point of the three clocks. Guidance values are defaults, not
dogma — tune against reality, but keep the *shape*.

- **Measurement (1–2 Hz, background)**: for each detection box, depth-sample a 5×5 grid
  inside the box; reject taps deviating > ±25 % from the median depth (background);
  fit an oriented bounding box (XZ min-area fit about gravity-aligned Y + height extent).
- **Object persistence**: match a new measurement to an existing object when centers are
  within `max(0.15 m, 0.5 × mean extent)`; otherwise mint a new id. Objects expire after
  ~10 s unseen (unless anchored by a placed experience).
- **Identity attach (~1 Hz)**: on `vision.identification`, project each object's OBB into
  the identified frame (`frameTimestamp` selects the pose), compute IoU with each
  detection box, attach label to the best match with IoU ≥ 0.3.
- **Label debounce**: adopt a label after 2 consecutive ticks agreeing, or immediately at
  confidence ≥ 0.8; a different label must win the same debounce to replace it.
- **Parts**: resolve each `parts[].box` through the same depth-grid path, clamped inside
  the parent OBB → a `region` (sub-OBB). Fallback when the VLM gives no box and the part
  label ∈ {cap, lid, top}: top 20 % slice of the parent OBB. A placement targeting
  `{objectId, part}` anchors to the part region's center, oriented to the parent.
- **Support surface**: an object's `supportSurfaceId` is the surface whose top plane is
  within 4 cm below the OBB's bottom face and overlaps it in XZ.

## 4. Swift core types

`ios/Spatail/Sources/Core/SpatailCore.swift` (checked in) defines the wire-aligned
Codable types every module shares: `SurfaceKind`, `OrientedBox`, `RoomSurface`,
`SpatailPart`, `SpatailObject`, `Detection2D`, `ResolvedDetection`, `BrainEndpoints`.
Modules do not redefine these; wire Codables in `Sources/Contracts/` map to/from them
only where legacy field names differ.

## 5. Perception v3 additions (2026-07-05 — see `PERCEPTION_V3.md`)

All additive; old peers ignore unknown fields/messages. Normative details live in
`docs/xr/PERCEPTION_V3.md` §8.

### 5.1 `room.update.objects[]` extensions (phone → PC)

- `parentId` — supported-by relation (laundry pile → couch). Children stay flat in
  the list with their own OBB/identity.
- `attributes` — `{colors[], materials[], textContent[], language, brand, state}`,
  the entity dossier (all fields optional).
- `form.kind` gains `"assembly"`; `form.primitives` = `[{name, obb}]` (world-space
  named primitives: seat/backrest/armrest_left/…); `form.source` gains `"roomplan"`.

### 5.2 `vision.focus` (phone → PC) / `vision.focus.result` (PC → phone)

Question-driven perception: a hi-res crop of ONE object + what's wanted from it.

```json
{"type": "vision.focus", "payload": {
  "requestId": "f-…", "objectId": "<registry uuid>",
  "question": "what language are the keys?",
  "wanted": ["textContent", "language", "colors"],
  "jpegBase64": "…", "frameTimestamp": 1783036800.123 }}
```

```json
{"type": "vision.focus.result", "payload": {
  "requestId": "f-…", "objectId": "<uuid>", "label": "mechanical keyboard",
  "confidence": 0.92,
  "attributes": {"colors": ["blue", "black"], "language": "English",
                  "textContent": ["QWERTY"]},
  "answer": "The keycaps are labeled in English.",
  "parts": [{"label": "escape key", "box": [x1, y1, x2, y2]}],
  "latencyMs": 1400, "model": "qwen2.5-vl" }}
```

Focus `parts[].box` / any boxes are in the CROP's pixel space (xyxy corners, same
convention as §1.3) — the phone converts back through the crop rect it sent.
Delivered ONLY to the requesting socket. One focus in flight per phone;
latest-wins per object.

### 5.3 `vision.identification` extension (PC → phone)

`payload.attributes` — the primary object's dossier fields, same shape as §5.1.
