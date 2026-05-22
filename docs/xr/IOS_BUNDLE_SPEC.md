# SPATAIL → iOS AR Bundle Spec (v0.5)

The iOS app is a **generic player**. Each "explanation" is a self-contained `.spatail` bundle shipped from Blender. **The app binary does not change per prompt.** New explanations = new bundles, dropped into the app via URL, AirDrop, share sheet, or Files.

This document is the contract: what's inside a bundle, and what the iOS app must do with each piece.

---

## 1. Bundle layout

A `.spatail` bundle is a ZIP archive with the `.spatail` extension. Mounted, it looks like:

```
my_explanation.spatail/
├── manifest.json              ← top-level bundle manifest (THIS spec)
├── experience.json            ← v0.5 SpatialExperienceContract (full contract)
├── scene.usdz                 ← geometry + materials + baked animation
├── prims_index.json           ← prim-name → contract-element-id map
├── hero/
│   ├── front.jpg              ← preview frames for cover / loading
│   ├── perspective.jpg
│   └── thumbnail.jpg          ← 256×256 cover for the picker
├── narration/                 ← optional per-step audio
│   └── <step_id>.m4a
└── source/
    └── prompt.txt             ← original user prompt (read-only, for credits)
```

### Why ZIP-as-`.spatail`

- Single-file share. AirDrop / Files / iCloud all handle single files cleanly.
- Custom UTI (`com.spatail.experience`) registered by the app → tap opens it in-app.
- The user can still rename to `.zip` and inspect contents — no obfuscation.

---

## 2. `manifest.json`

Top-level pointers + version pinning. Kept small so the picker can render a list of bundles without parsing the heavy contract.

```json
{
  "schemaVersion": "0.5.0-spatail-bundle",
  "experienceId": "f1_wheel_buttons_walkthrough",
  "title": "What do all the buttons on an F1 steering wheel do?",
  "createdAt": "2026-05-22T14:30:00Z",
  "source": {
    "asset": "f1_steering_wheel",
    "prompt": "source/prompt.txt"
  },
  "files": {
    "experience": "experience.json",
    "scene": "scene.usdz",
    "primsIndex": "prims_index.json",
    "thumbnail": "hero/thumbnail.jpg"
  },
  "scene": {
    "unitScale": 1.0,
    "upAxis": "Y",
    "boundingBoxMeters": [0.32, 0.18, 0.12],
    "defaultViewerDistanceMeters": 0.6,
    "supportsRealScale": true,
    "supportsTabletop": true
  },
  "narrationLanguages": ["en"]
}
```

The iOS app reads **manifest.json first** and uses it to populate the picker. Only when the user opens the bundle does it parse `experience.json`.

---

## 3. `scene.usdz`

Apple's native AR format. Loaded directly into RealityKit (`Entity.load(contentsOf:)`).

**Authoring rules the Blender exporter follows:**
- **Up axis = Y, units = meters.** USDZ assumes this. Blender's Z-up is rotated at export.
- **One root prim** named `/Scene` containing all logical parts.
- **Each logical part is its own prim** at `/Scene/<part_id>`. The `part_id` matches keys in `prims_index.json` and `experience.json::spatialElements[].id`.
- **Animation tracks** are baked as USD `TimeSamples`. Multiple animation clips → multiple `/Anims/<anim_id>` scopes. The iOS app starts/stops them by name.
- **Materials** are USD Preview Surface (`UsdPreviewSurface`) — RealityKit consumes these without converters.
- **No scripted constraints.** Damped Track / IK / Drivers are baked at export. The bundle is a flat playback artefact.

---

## 4. `prims_index.json`

Map from USDZ prim paths to contract element IDs. The iOS app uses this to wire taps on a prim into the contract's interaction layer.

```json
{
  "/Scene/wheel_face": "wheel_face",
  "/Scene/paddle_left": "paddle_left",
  "/Scene/rotary_mode": "rotary_mode",
  "/Scene/button_engine_brake": "button_engine_brake"
}
```

Reverse direction (id → prim) is also populated. The map is precomputed at export so the iOS app does not have to traverse the USD scene graph.

---

## 5. `experience.json`

The full v0.5 SpatialExperienceContract (see `pipeline/spatail/experience_contract.js` for the schema). The iOS app implements renderers for:

### Required mechanics (v1 ship list)

| Mechanic kind | iOS renderer |
|---|---|
| `annotated_callouts` | Tap-to-show floating labels anchored to prims |
| `highlighted_region` | Emissive throb on a prim's material |
| `exploded_view` | Radial-from-centroid animation on a group of prims |
| `cross_section` | Clipping plane in the shader |
| `assembly_sequence` | Stepped explode → reassemble sequence with narration triggers |
| `timeline` | Floating horizontal step strip with progress dots |
| `ghosted_internal` | Opacity ramp on the outer-shell prim group |

The remaining 16 mechanics (`particle_flow`, `force_arrows`, etc.) ship in later iOS milestones. If `experience.json` requests an unshipped mechanic, the app renders a `placeholder_mechanic` stub showing the mechanic's `params.title`.

### Animation primitives

The iOS app implements all 7 in `ANIMATION_PRIMITIVES`:
- `transform_keyframes` → RealityKit reads the USDZ track directly
- `explode` / `assemble` → computed at runtime against prim bounding boxes
- `highlight_pulse` → emissive shader animation
- `fade` → opacity tween
- `set_visible` → entity `isEnabled` toggle
- `attention_camera_hint` → animate the AR session's camera/focus dolly

### Interactions

Triggers (`tap`, `hover`, `dwell`, `scene_event`) and actions (`play_animation`, `set_visible`, `advance_step`, …) wire into the sequence controller.

### Presentation layouts

For iOS AR (handheld), only `stage_in_front` is meaningful in v1. `wall_room` / `scene_floor` are visionOS-only.

---

## 6. Anchor strategies the iOS app supports

| Strategy | iOS behavior |
|---|---|
| `world_anchor` | `ARWorldAnchor` at first-pose offset 0.6m ahead of camera |
| `plane_anchor` | `ARPlaneAnchor` from `ARPlaneDetection.horizontal` |
| `object_anchor` | Not in v1 — falls back to `world_anchor` |
| `relative_to_target` | Computed relative to another element's resolved pose |
| `user_relative` | Updated each frame from the camera transform |
| `simulated_anchor` | `world_anchor` with a fixed offset |

---

## 7. Scale modes

| Mode | iOS treatment |
|---|---|
| `real_scale` | Spawn at the bundle's `boundingBoxMeters` 1:1 |
| `tabletop_scale` | Scale so the longest axis is 30 cm; require a horizontal plane |
| `enlarged_detail` | Scale to 1.5× of real |
| `compact_panel` | 2D panel mode — pinned at 0.4m, 12×18cm |
| `room_scale` | Fallback to `real_scale` on iOS (no room awareness in v1) |

---

## 8. Loading flow (iOS app)

1. User opens a `.spatail` (tap in Files, URL handler, share extension).
2. App unzips to a sandboxed cache dir.
3. Read `manifest.json` → validate `schemaVersion`.
4. Parse `experience.json` → instantiate sequence controller, prepare mechanic renderers.
5. Load `scene.usdz` into a root `Entity`.
6. Hide all entities not in the first sequence step's reveal list.
7. Start AR session, anchor per the contract.
8. Run `defaultSequenceId` (or wait for user tap to start).

If any of steps 3–5 fail with a missing/invalid file, the app shows the bundle's `hero/perspective.jpg` plus the `explanation.written` text as a static fallback — the user always sees *something*.

---

## 9. Versioning

`schemaVersion` is a hard gate:
- App declares its `supportedBundleSchemaVersions` (e.g. `["0.5.0-spatail-bundle"]`).
- Mismatched bundle → show a "this bundle was made for a newer version of the app" sheet with a link to update.

Schema bumps land in lockstep with Blender exporter changes, both sides flip together.

---

## 10. What the iOS app does NOT do

- **Generate explanations.** All content is in the bundle.
- **Run an LLM.** No on-device or remote inference required to render.
- **Edit bundles.** Read-only player.
- **Replan placements** at runtime. Placements are baked at authoring time.

The "no code generation, no app rebuild per prompt" property holds as long as every new explanation only uses primitives in the closed vocabularies. The vocab lives at the top of `experience_contract.js` and is published in-band on each bundle (`experience.json.vocabularies`).
