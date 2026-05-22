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
