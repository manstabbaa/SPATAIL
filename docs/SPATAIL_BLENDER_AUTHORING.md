# SPATAIL Blender Authoring Spec

How a Blender author teaches the SPATAIL viewer a sequence.

> **The pipeline reads conventions, not magic.** No registry edits per
> asset. Open the `.blend`, name your NLA strips + timeline markers per
> the rules below, and the exporter does the rest.

## Per-scene setup

Every `.blend` exported for SPATAIL needs one empty named **`SPATAIL_meta`**
with the following custom properties (right panel → Object Properties →
Custom Properties):

| key                       | type   | meaning                                                            |
| ------------------------- | ------ | ------------------------------------------------------------------ |
| `assetId`                 | string | matches the contract's element id or `assetGroupRef`               |
| `defaultSequenceId`       | string | sequence the viewer picks up on load                               |
| `targetElementId`         | string | which contract element this `.blend` animates (often = assetId)    |
| `loopDefault`             | bool   | when no sequence is playing, autoplay the `default.*` strips       |
| `attentionCameraCollection` | string | name of the camera-path collection (see below)                    |

If `SPATAIL_meta` is missing the exporter falls back to filename-derived
ids and a sensible default sequence id.

## NLA strip naming

The strip name **is** the contract reference. Three prefixes are
honoured; everything else is ignored.

### `seq.<sequence_id>.<order>.<label>`

Fires as a step inside a sequence. Order is a two-digit zero-padded
integer (`00`–`99`). Label is free text — exporter slugs it for the id.

```
seq.steering_walkthrough.00.intro_fade_in
seq.steering_walkthrough.03.explode_rim
seq.steering_walkthrough.07.assemble
```

The exporter sorts steps by `<order>`, computes durations from strip
start/end, bakes the action, and emits a `sequence_hints.json` entry
the planner drops into `contract.sequences[]`.

### `interaction.<interaction_id>`

Fires on a user interaction. The id matches an entry in the contract's
`interactions[]`. Example: `interaction.tap.rim` plays when the user
taps the rim callout.

### `default.<label>`

Ambient / idle. Loops while no sequence step is active and
`loopDefault` is true. Example: `default.idle_rotation` for a slow
wheel spin.

## Timeline markers

Markers add named cue points the exporter pulls into a step's
`atFrame` array. Use them to drive viewer beats that need to land on
specific moments inside a long bake (e.g. "fade the panel in exactly at
the apex of the camera dolly").

Marker naming: `cue.<sequence_id>.<event>` — e.g. `cue.steering_walkthrough.panel_in`.

## Camera paths (collections)

Author a viewer camera dolly per sequence step by:

1. Creating a **collection** named `attention.cam.<sequence_id>`.
2. Adding a `Camera` to it with keyframes on location + rotation.
3. Optionally adding empties as look-at targets named
   `lookat.<step_order>` — the exporter samples them as the dolly's
   target trajectory.

The exporter samples this camera (and its look-at) at
`animation.cameraPathSamples` frames per second of action, and writes
the samples into `animations.json` under a `camera_path` primitive.
The viewer's `camera_path` handler smoothly tweens the orbit camera
between samples.

## Material / light / shape-key animation

Anything glTF can carry (transform, morph targets, skinning) rides
inside the GLB the exporter writes — no extra setup. For the rest:

- **Material parameters** (emission strength, base color, roughness …):
  keyframe the node in the shader graph. The exporter samples those
  parameters at `animation.materialBakeMode`'s cadence and emits
  `apply_baked_track` animations.
- **Light parameters** (intensity, color): same treatment. The handler
  applies the values to the loaded scene's lights matched by name.
- **Shape keys**: keyframe them, the GLB carries them, three.js plays
  them via the AnimationMixer the viewer already wires up.

## Quality knobs (`spatail.config.json`)

The exporter honours these. Bump them when you need cinematic, leave
them alone for fast iteration:

```json
{
  "animation": {
    "frameRate": 30,
    "bakeStepFrames": 1,
    "materialBakeMode": "continuous",
    "cameraPathSamples": 60
  }
}
```

| key                 | effect                                                              |
| ------------------- | ------------------------------------------------------------------- |
| `frameRate`         | timeline FPS used for sec/frame math (30 default; 60 for cinematic) |
| `bakeStepFrames`    | bake every Nth frame — 1 is per-frame, 3 is "loop is fine sparser"  |
| `materialBakeMode`  | `"keyframe"` only writes authored keys; `"continuous"` resamples    |
| `cameraPathSamples` | frames per second resampled into the camera_path baked track        |

## What the exporter produces

For an input `wheel.blend` the exporter writes three artefacts under
`assets_processed/animations/<assetId>/`:

```
<assetId>.glb                 # GLB with all baked transform / morph tracks
<assetId>.animations.json     # everything glTF can't carry — material / light / camera samples,
                              # plus the interaction-wiring strips
<assetId>.sequence_hints.json # ordered sequence steps the planner drops into contract.sequences[]
```

The planner picks up `sequence_hints.json` when it builds the contract
for an experience whose `assetGroupRef` resolves to this asset, and
merges its steps with the existing primitive-based default sequence
(if any).

## Author → preview loop

```bash
# One-time: install Blender 4.x and put it where blender_runner finds it.

# After every authoring pass:
npm run spatail:animations -- assets_authoring/wheel.blend wheel
npm run spatail
npm run spatail:viewer
```

The viewer reloads the contract; press ▶ on the transport bar to watch
the new pass play through.
