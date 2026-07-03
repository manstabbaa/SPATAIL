# SPATAIL Realtime Protocol (v0.1)

The wire contract between the **iOS player** and the **SPATAIL server**. Replaces the "AirDrop a .spatail bundle" flow with a live WebSocket session:

```
prompt → understanding → Blender → room awareness → spatial placement
                              ↑          ↓
                              └──────────┘
                            (re-plans as the user moves)
```

The server runs continuously. Blender runs continuously as a worker. iOS opens a WebSocket, streams ARKit state, and receives experience updates as they're produced.

---

## 1. Endpoints

| URL | Method | Purpose |
|---|---|---|
| `wss://<host>/v1/session` | WebSocket | Bidirectional event stream (the hot path) |
| `https://<host>/v1/assets/<bundleId>/scene.usdz` | GET (signed) | Download USDZ when scene changes |
| `https://<host>/v1/assets/<bundleId>/hero.jpg` | GET (signed) | Cover frame |
| `https://<host>/v1/health` | GET | Liveness |

Auth: bearer token in the `Authorization` header on connect. The token is issued by `POST /v1/auth/anon` for unauthenticated sessions during dev.

---

## 2. Wire format

Every WebSocket message is a single JSON object with a fixed shape:

```json
{
  "type": "<event_type>",
  "seq": 142,
  "sentAt": "2026-05-23T10:14:55.123Z",
  "payload": { ... }
}
```

- `type` is from a closed vocabulary (sections 3 + 4).
- `seq` is a monotonic counter scoped to the sender. The receiver may use it to detect drops.
- `sentAt` is ISO-8601 UTC. Used for telemetry and replay; not for ordering (use `seq`).
- `payload` is event-specific.

Messages exceeding 64 KB are rejected. Larger artefacts ride over HTTP (USDZ, hero JPGs).

---

## 3. iOS → Server events

### `session.start`
Sent immediately after the WS handshake.

```json
{
  "type": "session.start",
  "payload": {
    "client": { "platform": "ios", "appVersion": "0.1.0", "osVersion": "17.4" },
    "capabilities": {
      "arkitVersion": "6",
      "hasLidar": true,
      "supportsRoomCapture": true,
      "supportsRealityKit2": true
    },
    "supportedBundleSchemaVersions": ["0.5.0-spatail-bundle"]
  }
}
```

### `user.prompt`
The user's question. Can include an audio reference for later TTS sync.

```json
{
  "type": "user.prompt",
  "payload": {
    "text": "What do all the buttons on an F1 steering wheel do?",
    "audioUrl": null,
    "context": { "previousExperienceId": null }
  }
}
```

### `room.update`
ARKit room geometry. Sent on first detection and on significant change. **Delta only** — the server holds the prior state.

```json
{
  "type": "room.update",
  "payload": {
    "version": 3,
    "kind": "delta",
    "added":   [ { "id": "plane_4", "kind": "horizontal", "centroid": [0.0, 0.0, -0.7], "extent": [1.2, 0.8], "normal": [0, 1, 0] } ],
    "changed": [ { "id": "plane_1", "centroid": [0.1, 0.0, -0.5] } ],
    "removed": [ "plane_2" ],
    "userPose": { "position": [0, 1.6, 0], "forward": [0, 0, -1] }
  }
}
```

Field shape matches the existing `roomContract` consumed by `pipeline/spatail/room_aware_planner.js` — the server pipes this straight through.

### `pose.update`
Throttled to 5 Hz. The server uses this to feed `attention_camera_hint` animation primitives and to re-resolve `user_relative` anchors.

```json
{
  "type": "pose.update",
  "payload": { "position": [0.1, 1.6, 0.0], "forward": [0, 0, -1], "right": [1, 0, 0] }
}
```

### `interaction.tap`
The user tapped a prim in the scene. The server resolves it through the contract's `interactions[]`.

```json
{
  "type": "interaction.tap",
  "payload": { "elementId": "rotary_mode", "tapWorld": [0.12, 1.4, -0.6] }
}
```

### `session.end`
Graceful close. Server may also close on idle timeout (15 min default).

```json
{ "type": "session.end", "payload": { "reason": "user_left" } }
```

---

## 4. Server → iOS events

### `session.ready`
Acknowledges the session and confirms server capabilities.

```json
{
  "type": "session.ready",
  "payload": {
    "sessionId": "sess_01HJ5...",
    "serverVersion": "0.1.0",
    "bundleSchemaVersion": "0.5.0-spatail-bundle"
  }
}
```

### `understanding.partial`
Intermediate orchestrator state — lets iOS show a progress chip ("identifying asset…", "classifying parts…"). Optional; iOS can ignore.

```json
{
  "type": "understanding.partial",
  "payload": {
    "stage": "classify",
    "label": "F1 steering wheel detected",
    "progress": 0.4
  }
}
```

### `asset.url`
Tells iOS where to download the USDZ for the current experience. The URL is signed and short-lived (5 min). iOS caches per `bundleId`.

```json
{
  "type": "asset.url",
  "payload": {
    "bundleId": "sess_01HJ5_v1",
    "sceneUsdz": "https://.../scene.usdz?sig=...",
    "heroThumbnail": "https://.../thumbnail.jpg?sig=...",
    "byteSize": 8649681,
    "etag": "W/\"abc123\""
  }
}
```

### `experience.delta`
The v0.5 SpatialExperienceContract or a delta to it. **First message after `asset.url`** is always full (`kind: "full"`). Subsequent re-placements (room change, pose drift) ship `kind: "patch"` with only the changed elements.

```json
{
  "type": "experience.delta",
  "payload": {
    "version": 7,
    "kind": "full",
    "experience": { "schemaVersion": "0.5.0-spatail", "spatialElements": [...], ... }
  }
}
```

```json
{
  "type": "experience.delta",
  "payload": {
    "version": 8,
    "kind": "patch",
    "patches": [
      { "op": "replace", "path": "/spatialElements/0/placement", "value": { "kind": "above_target", "offset": [0, 0.2, 0] } },
      { "op": "add", "path": "/interactions/-", "value": { "id": "tap_brake", "trigger": "tap", "target": "button_engine_brake", "actions": [...] } }
    ]
  }
}
```

Patch operations follow [RFC 6902 JSON Patch](https://datatracker.ietf.org/doc/html/rfc6902).

### `narration.chunk`
Optional TTS audio for the current sequence step. Either a URL or inline base64 for tiny clips.

```json
{
  "type": "narration.chunk",
  "payload": { "stepId": "step_3", "audioUrl": "https://.../narr/step_3.m4a", "durationMs": 4200 }
}
```

### `error`
Non-fatal errors are reported here. Fatal errors close the WebSocket with code 4001-4099.

```json
{
  "type": "error",
  "payload": { "code": "blender_busy", "message": "Worker is rendering another asset; retry in 6s", "retryAfterMs": 6000 }
}
```

Error codes (closed set):
- `bad_message` — JSON parse / schema fail
- `prompt_blocked` — moderation rejection
- `asset_not_found` — referenced bundle expired
- `blender_busy` — worker contention
- `blender_failed` — worker crash / timeout
- `understanding_failed` — orchestrator error
- `room_required` — server needs room data for the requested experience kind

---

## 5. Lifecycle

```
iOS                          Server                       Blender
 │                            │                              │
 ├── WS connect ──────────────►                              │
 ├── session.start ───────────►                              │
 │                            │── pool.acquire ────────────►│
 │◄── session.ready ──────────│                              │
 │                            │                              │
 ├── user.prompt ─────────────►── orchestrator.understand    │
 │                            │── run skills ───────────────►│
 │◄── understanding.partial ──│  (treat→merge→classify…)     │
 │◄── understanding.partial ──│                              │
 │                            │◄── pipeline done ───────────│
 │◄── asset.url ──────────────│                              │
 │ (downloads USDZ)           │                              │
 │◄── experience.delta(full) ─│                              │
 │ (loads scene)              │                              │
 │                            │                              │
 ├── room.update ─────────────►── room_aware_planner.replan  │
 │◄── experience.delta(patch)─│                              │
 │                            │                              │
 ├── interaction.tap ─────────►── interaction dispatch       │
 │◄── experience.delta(patch)─│  (sequence advance, etc.)    │
 │                            │                              │
 ├── session.end ─────────────►                              │
 │ ←── WS close ──────────────│── pool.release ────────────►│
```

---

## 6. Ordering & idempotency

- **`asset.url` always precedes `experience.delta(full)`** so iOS has geometry loaded before placements apply.
- **`experience.delta(patch)` is monotonic by `version`.** If iOS sees v=8 before v=7, it requests a full snapshot via `session.resync`.
- **Server is idempotent on `room.update`.** Sending the same delta twice has no effect.

### `session.resync` (iOS → Server)

```json
{ "type": "session.resync", "payload": { "knownVersion": 6 } }
```

Server replies with a fresh `experience.delta(full)` at the current version.

---

## 7. Throttling

| Event | Rate cap | Notes |
|---|---|---|
| `pose.update` | 5 Hz | iOS-side throttle; server drops excess |
| `room.update` | 1 Hz | Coalesce deltas within 1s |
| `user.prompt` | 1 every 2s | Avoid orchestrator flood |
| `experience.delta` | 2 Hz | Server-side coalescing |

---

## 8. Future room-aware extensions

- **`object.anchor.set`** — when ARKit `ARObjectAnchor` detects a real-world object the experience can anchor onto (the user's actual steering wheel rig).
- **`hand.update`** — once visionOS or iOS hand-tracking lands; trigger gestures beyond tap.
- **`gaze.update`** — for visionOS `dwell` triggers.
- **`audio.transcript.partial`** — for live voice prompts, server-side ASR streaming.

These all use the same envelope (type / seq / sentAt / payload). The closed vocab is bumped one version at a time.

---

## 9. Why not pure HTTP polling?

- Re-placement on room change should feel **instant** (≤ 200 ms). Polling can't hit that.
- The orchestrator emits partial updates as Blender runs (10–30 s for a fresh asset). Streaming them keeps the user oriented.
- A WS connection is the same channel for input (room/pose) and output (experience updates) — simpler than HTTP + Server-Sent-Events.

Bundle download is still HTTP because: cacheable, signed-URL revocation, CDN-friendly, parallel range requests. Only the **control plane** is WS.

---

## 10. Vision uplink + live identification (v0.2 — the TRACKED stream)

> **Normative spec:** the live-brain wire & fusion contract (message shapes, env-var names, gate defaults, trace paths, on-device fusion semantics) is `docs/xr/LIVE_BRAIN_SPEC.md`. This section describes the channel; where the two disagree, the spec wins.

v0.1 understands the world from a *typed prompt* + room geometry. v0.2 adds a **vision channel**: the phone streams camera frames to a **PC live intelligence engine** that runs a VLM (NVIDIA NIM / Ollama / vLLM via an OpenAI-compatible endpoint) and answers *"what is the user looking at?"*. Implemented by `pipeline/server/spatail_vision_engine.py`.

**Why a separate channel (not the WS control plane):** camera frames are far larger than the 64 KB control-plane cap. They ride a **dedicated binary WebSocket**; only small identification results return on the control plane (or that same socket).

**Latency split (the key design rule):** the VLM sets *what + roughly where* at ~1–3 Hz. **Continuous 6-DoF pose/anchoring stays on-device** (ARKit world tracking now; iOS 27 `ARObjectAnchor`+`isTracked` later). Never send per-frame pose over the network.

### Endpoints (engine)

| URL | Purpose |
|---|---|
| `ws://<pc>:8798/v1/vision` | Binary JPEG frames up; `vision.identification` JSON down |
| `http://<pc>:8799/` | Browser **debug view** — live frame + boxes + latency (watch over Parsec) |
| `http://<pc>:8799/frame.jpg`, `/state.json` | Raw latest frame + engine state (what the overlay polls) |

### `vision.frame` (iOS → engine)
Not a JSON envelope — a **raw binary WebSocket message** whose bytes are a downscaled JPEG (~640 px long edge, ~2–5 Hz). Keeping it binary avoids base64 bloat. Text messages on this socket carry the fusion control plane (`room.update` / `pose.update` — §10.1).

### `vision.identification` (engine → iOS)
```json
{
  "type": "vision.identification",
  "sentAt": "2026-06-25T22:40:00.000Z",
  "payload": {
    "primary": "car engine air filter",
    "detections": [
      { "label": "car engine air filter", "confidence": 0.82, "box": [0.31, 0.28, 0.4, 0.35] },
      { "label": "airbox", "confidence": 0.4, "box": null }
    ],
    "rawText": "{...}", "latencyMs": 940, "model": "qwen2.5vl:7b",
    "frameTimestamp": 1783036825.412,
    "parts": [ { "label": "air intake hose", "box": [0.55, 0.30, 0.2, 0.18], "confidence": 0.7 } ]
  }
}
```
`box` is normalized `[x, y, w, h]` (origin top-left) or `null` — treat it as a hint; chat VLMs are unreliable at precise boxes. `frameTimestamp` echoes the capture/ingest time of the frame this identity describes; `parts` are the primary object's visible sub-parts (same box space, may be `[]` — the engine parses them defensively so a creative VLM reply never breaks identification). On device, identity attaches to tracked objects by projecting each object's OBB into the `frameTimestamp` frame and taking the best IoU against `detections[].box`, with part boxes resolved through the depth-grid path (LIVE_BRAIN_SPEC §3) — this replaces the earlier "raycast the box centre" design.

### Convergence with the PLACED stream
A confident `vision.identification` can auto-fire the equivalent of a `user.prompt` (`"explain this <primary>"`) into the v0.1 prompt→Blender pipeline, so the explanation builds for whatever the camera is pointed at — no typing. That gate is now real in the engine: replans fire through the binding gate of §10.1 (`SPATAIL_REPLAN_MIN_CONF`, default 0.55; `SPATAIL_REPLAN_DWELL`, default 2 consecutive identifications) per LIVE_BRAIN_SPEC §1.4.

### Observability (the "joint activity" surface)
The engine renders an HTML overlay at `http://<pc>:8799/` (latest frame + detection boxes + labels + ingest fps + per-call latency + raw VLM text). Open it over Parsec to watch the pipeline end-to-end while you point the phone. `--test-dir <imgs>` feeds local images so the engine + VLM + overlay can be verified on the PC **before** the phone is wired.

### 10.1 Fusion loop (v0.3) — room up, placement down

The text channel on the vision socket is no longer reserved: it carries the **fusion control plane**. The phone streams its room scan up; the engine fuses the latest VLM identification with the scanned geometry (via `pipeline/spatail/plan_from_room.js` → `surface_fusion.js` + `surface_placement.js`) and answers with a placement contract.

**`room.update` (iOS → engine, text on `ws://<pc>:8798/v1/vision`):**
```json
{
  "type": "room.update",
  "payload": {
    "room": { /* an iOS RoomContract (0.4.0-spatail-room) or brain-shaped room */ },
    "pose": { "position": [0, 1.5, 1.6], "forward": [0, -0.45, -0.89] },
    "objects": [ { "id": "8C1A…", "label": "water bottle", "confidence": 0.91,
                   "obb": { "center": [0.2, 0.8, -0.5], "extents": [0.08, 0.24, 0.08], "yaw": 0.42 },
                   "supportSurfaceId": "surf-…", "lastSeenAt": 1783036800.123 } ],
    "debugIdentification": { "primary": "table", "detections": [] }
  }
}
```
`room` accepts the device's persisted RoomContract verbatim (`room_contract_adapter.js` normalizes it). `pose` is the camera at send time — the brain raycasts it through the scanned surfaces (`surface_fusion.js`: the camera-forward ray, kind-gated by the VLM noun, picks the frontmost pierced surface). `objects` is the device ObjectRegistry's tracked-object list (LIVE_BRAIN_SPEC §1.2); the engine hands it through to the brain so placements can bind to real objects, objects-first. `debugIdentification` is a dev hook that pins the VLM answer for tests; live traffic omits it and the engine uses its latest `vision.identification`.

**`pose.update` (iOS → engine, same socket, ≤ 2 Hz):** `{"type": "pose.update", "payload": {"position", "forward", "up", "timestamp"}}` keeps the brain's gaze ray live between room sends. It updates the held pose only (owner-gated) and **never** triggers a replan (LIVE_BRAIN_SPEC §1.1).

**`experience.delta` (engine → iOS, same socket):** same envelope as §4 — `payload.kind = "full"`, `payload.experience` = a SpatialExperienceContract whose elements use the v0.6 `surface_edge` / `surface_corner` placements (with `surfaceRef`, `from`/`to`, `count`, `spanMeters`). The engine replans on every `room.update`; identification-driven replans are gated on the **binding** — the label's mapped surface kind (mirroring `labelToSurfaceKind` in `surface_fusion.js`) or the matched tracked-object id — changing, with confidence ≥ `SPATAIL_REPLAN_MIN_CONF` (default 0.55) and the same binding held for `SPATAIL_REPLAN_DWELL` (default 2) consecutive identifications. Raw label re-wordings with the same binding never replan (LIVE_BRAIN_SPEC §1.4). Replans run as detached asyncio tasks (serialized by a plan lock) so the identification downlink never blocks on the brain, and every replan is archived to `studio/out/traces/vision/<session>/plan_NNNN.json` — `brainInput` replays offline through `plan_from_room.js --stdin` (LIVE_BRAIN_SPEC §2.1). The `:8799` overlay and `/state.json` expose the gate state (planned/candidate binding, dwell, confidence), the tracked-object list, and identification age.

**Room ownership (v0.3.1):** the room is scoped to the **connection that sent it**. `experience.delta` is sent **only to that socket** — never broadcast — because its coordinates only mean something in the sender's ARKit session. Identification-triggered replans run only while the owner is still connected. When the owner disconnects (and if an ownerless room is ever found when a new client connects), the engine clears the held room, pose, plan, and plan version, so a fresh session can never receive placements computed against another session's coordinate system. `vision.identification` remains a broadcast to every connected client. iOS additionally ignores `experience.delta` until it has sent a `room.update` on the current connection.

**`GET http://<pc>:8799/contract`** serves the latest plan (`{mode, summary, fused, contract}`) for the web mirror and curl checks; it 404s until the current owner's room has produced one (an engine restart or owner disconnect resets it).
