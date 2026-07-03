# THE SPATAIL ENGINE — canonical spec

> **Normative. Adopted 2026-07-03.** This document is the engine's single source of
> truth. It is referenced from the Master File (`docs/spatail_2026_mf_pivot.md` §6g/§6h)
> and from `CLAUDE.md`. Companions: `docs/xr/LIVE_BRAIN_SPEC.md` (live wire & fusion),
> `docs/spatail-placement-design-system.md` (placement policy),
> `ios/SpatailEngine/README.md` (the Swift core's own build notes).
>
> Honesty rule for this file: everything is labelled either **(built)** — verified in
> this repo today — or **(direction)** — normative but not yet landed. No aspirational
> claim is stated as fact.

---

## 1. Identity — a contract-driven runtime

**The brain authors DATA; the engine executes it. The brain never emits code.**

Every SPATAIL experience is a JSON contract written in **closed vocabularies** —
finite, named enums for representation, placement, animation, triggers, natures,
genres. The engine is the runtime that gives that data behaviour. This is the one
architectural law everything else follows from:

- **Closed, not open.** A vocabulary value the engine doesn't know is ignored or
  degraded visibly — never `eval`'d, never guessed into fake behaviour. Unknown
  action kinds are ignored (forward-compatible); unknown natures fall back to `still`;
  a missing renderer shows a labelled stub, not a lie.
- **The declarative/imperative line never moves.** Continuous numerical simulation —
  physics, projectile trajectories, collision, the 60 Hz tick, clip sampling, AR
  tracking — is **native engine code** (a `System` or the backend). Structure,
  thresholds, which-system-runs, and discrete *when X happens do Y* reactions are
  **data the brain emits**. That is how one engine honestly runs a declarative
  explainer and a real-time shooter.
- **Every decision is explainable.** Contracts carry `whyThisRepresentation`,
  `whyThisPlacement`, `decisionTrace`; clients report back what they actually did
  (`POST /placement-report`). If you can't see what it saw, it doesn't ship.

There is deliberately **no expression VM, no scripting language, no generic
JSON→scene-graph decoder**. Conditions are `path op value`; if a real genre proves
the closed form insufficient, we add one typed predicate — not a grammar.

---

## 2. Architecture — ONE spec, TWO backends

The engine is a specification with two living implementations. Both consume the same
brain emissions; neither is a port of the other's code, both are implementations of
this spec.

### 2.1 The Swift engine (built)

`ios/SpatailEngine` — two SwiftPM targets:

- **SpatailEngineCore** — a pure Swift simulation. Never imports RealityKit / ARKit /
  Metal / SwiftUI. ECS-lite substrate (`World`, `Entity`, `Component`, `Value`
  blackboard, path resolution), per-frame `System`s (`RuleSystem`, `ObjectiveSystem`,
  `HealthSystem`, `LifetimeSystem`, `SpawnerSystem`, `MoverSystem`,
  `StepSequencerSystem`), genre `Kit`s, and the `SpatailEngine` facade
  (`load(contract)` / `submit(event)` / `tick(dt)`).
- **SpatailEngineRealityKit** — the iOS/visionOS adapter. Implements `SceneBackend`,
  applies `BackendCommand`s (`createVisual`, `playClip`, `applyEffect`, `addPhysics`,
  `applyImpulse`, `anchor`, `speak`, `hud`, …) and pushes `EngineEvent`s back.

The seam is one protocol: `EngineEvent → SpatailEngineCore → BackendCommand →
SceneBackend`. **Tested headless** — `swift test` runs `EngineTests`, which prove a
shooter contract end-to-end (fire → projectile → collision → damage → death → score →
objective → win), an explainer stepping its sequence, and contract-authored
rules+objectives driving a collect-to-win loop, with no device and no renderer.

The product app (`ios/Spatail`) hosts this engine: its `ExperienceRuntime` is the thin
host that renders the modular contract's steps/assets today; folding step/trigger
execution fully onto the engine's kits is in flight **(direction)**.

### 2.2 The Web backend (built)

`webxr/viewer.js` — the WebXR viewer runtime (JavaScript / Three.js). It began as the
PC dev surface and is, in truth, **the de-facto second backend**: it consumes live
`/modular` contracts and executes a real subset of the engine spec. Naming it what it
is creates the obligation to converge it honestly.

**What the Web backend already implements (built):**

| Spec subsystem | In `webxr/viewer.js` |
|---|---|
| Aliveness / nature runtime (§3.5) | `natureFor` + `updateLife` — the seven natures, gaze-attention tracking, per-nature existence (heartbeat pulse, celestial turn, mechanical run, stillness) |
| Affordance behaviours | `applyBehavior` — rotate / animate (GLB clip) / scale / isolate / explode / reset, driven from per-object control panels built from the contract's affordances |
| Placement-intent solving (§3.3) | `brainToWebScene` + `resolveLayout` — resolves the brain's anchor/layout/footprint INTENT (row/arc/stack/cluster/grid × table/floor/wall) into coordinates, honours `realScaleBaked` / `realSizeMeters`, strict-asset mode |
| Materialization (§3.6) | `spawnIn` particle coalescence on load; generation-job polling (`pollGeneration`) + GLB hot-swap |
| Verification duties | `POST /placement-report` (LIVE_BRAIN_SPEC §2.2, `client:"web"`); brain text vs web-solver text never blended in the identify overlay |
| Story / attention track | `stepAttention` over the contract's attention plan |

**What the Web backend lacks vs the Swift core (its convergence duties, direction):**

- **The rules/objectives/kits VM.** No `RuleSystem` / `ObjectiveSystem` / `KitRegistry`
  equivalent: it cannot yet run a v0.8 genre contract (entities, templates, spawn,
  blackboard, win/lose). Duty: implement the same closed rule/objective evaluation, or
  explicitly degrade (render the explainer projection and mark the unexecuted
  emissions in the identify overlay — never silently drop them).
- **Trigger execution.** The brain's `triggers[]` (§3.4) are not executed on the web;
  interaction today is affordance buttons + click-to-select. Duty: a trigger VM
  mapping `onTap/onGaze/onApproach` to the same action vocabulary.
- **Physics.** No physics integrator; shooter-class contracts are out of reach until
  one is adopted (or the kit degrades to a non-physics variant).
- **HUD command channel.** No generic `hud` surface for scores/objectives.

The rule for both backends: **implement, or explicitly degrade — never fake.**

### 2.3 Contract lineage (built)

The contracts the engine executes are versioned and tolerant:

| Contract | Where | Version |
|---|---|---|
| `spatialExperienceContract` (closed vocabularies published in-band) | `pipeline/spatail/experience_contract.js`, `schemas/` | `0.5.0-spatail` |
| Modular wire contract from `POST /modular` (understanding · assets · sequence · triggers · placement · optional `sceneContract` projection) | `studio/director/*`, `studio/server/job_server.py` | v0.5/v0.6 wire |
| `ExperienceContract` (strict superset: genre/kit/params/entities/templates/rules/objectives/steps) | `ios/SpatailEngine/.../ExperienceContract.swift` | `0.8-spatail-engine` |

Decoding is tolerant by design: every field defaults, `sequence` maps to `steps`, so a
v0.5/0.6 payload still decodes in the v0.8 engine. New capability = new **additive**
block or vocabulary entry, never a breaking rewrite (§4).

---

## 3. Subsystems

Each subsystem is one contract concept plus the runtime that executes it.

### 3.1 Kits — genres as data (built core, growing library)

A **Kit** is a genre expressed as: an ordered `System` list + baseline rules/objectives
the genre always provides + a param schema (`Kit.normalize`). The brain's two-stage
director job is: (1) understanding → pick `genre`/`kit`; (2) emit that kit's validated
params + entities + a closed-vocabulary rule/objective table. Adding a genre = writing
one Kit (and any new Systems it needs); there is no `if genre ==` anywhere in the core.

Kits map onto the Master File's 12-experience taxonomy (MF p15):

| MF taxonomy | Engine kit | Status |
|---|---|---|
| Presentation / Explainer | `ExplainerKit` | **built** (Swift core; step sequencer + rules + objectives) |
| Game / Shooter | `ShooterKit` | **built** (Swift core; fire/hit/score/win laws, headless-tested) |
| Inspection (exploded) | `InspectorKit` | direction |
| Simulation | `SimulatorKit` | direction |
| Comparison | `ComparisonKit` | direction |
| Guide | `GuideKit` | direction |
| Overlay | `OverlayKit` | direction |
| Dashboard | `DashboardKit` | direction |
| Story | `StoryKit` | direction |
| Sandbox | `SandboxKit` | direction |
| Training | `TrainingKit` | direction |
| Lesson | `LessonKit` | direction |

**The lockstep law:** a new kit lands **when — and only when — the brain learns to emit
that genre.** A kit without a brain emission is dead code; a brain emission without a
kit is a lie to the user. The two ship together, in both backends (or with an explicit
degrade in one, §4). Note that pieces of several "direction" kits already exist as
runtime behaviours outside the kit shape (the web viewer's explode/isolate is
Inspection's core; the attention track is Story's spine) — the kit work is largely
formalisation, not invention.

### 3.2 Staging — THE SETTING BLOCK and the setting law (adopted 2026-07-03)

**The setting law (canon):** *placement is computed against context — the real room
when there is one (AR), a brain-composed setting when there isn't (staged).*

An experience never floats in a void. In AR the context is the scanned `RoomModel` /
ObjectRegistry (LIVE_BRAIN_SPEC). When there is no real room — the PC viewer, a staged
demo, a headset without a scan — the **brain composes the context**: a setting that
fits the question. Ask about a V8 and the answer arrives in a garage, the car's hood
open, the experience anchored at the engine bay.

**Request side:** the `POST /modular` body gains an optional
`"context": {"mode": "staged" | "ar"}`. `"ar"` (or absent) = real-room context, **no
setting emitted**; `"staged"` = the brain composes the setting.

**Response side:** the contract gains an optional, additive `setting` block. This exact
shape is normative:

```json
"setting": {
  "id": "garage-v8-001",
  "why": "<one sentence: why this setting fits the question>",
  "elements": [
    {
      "id": "car",
      "subject": "sedan car with its hood open",
      "assetPath": "/assets/... .glb OR null",
      "generationJobId": "<job id> OR null",
      "footprintMeters": [w, h, d],
      "pose": {"position": [x, y, z], "yawDeg": 0}
    }
  ],
  "experienceAnchor": {
    "elementId": "car OR null",
    "name": "engine_bay",
    "position": [x, y, z],
    "yawDeg": 0
  },
  "ground": {"kind": "floor", "sizeMeters": [8, 8]},
  "ambiance": {"style": "3d-pastel", "light": "soft-day"}
}
```

Semantics (normative):

- **Units & frame:** metres, world space, ground plane at `y = 0`.
- **Element asset states:**
  - `assetPath` set → render the GLB.
  - `assetPath` null + `generationJobId` set → the asset is being generated on the PC;
    the client polls `/jobs/{id}` and hot-swaps when it lands.
  - `assetPath` null + `generationJobId` null → pending with a reserved footprint only
    (generation unavailable).
- **Pending is honest:** the client renders a pending element as a **clearly-labeled
  materializing spawn-in field** at its `footprintMeters` bounds — **NEVER a fake solid
  object** (§3.6).
- **Anchoring:** the experience's layout origin anchors at `experienceAnchor`
  position/yaw. When `elementId` is set, the anchor is a named site on that element
  (the wire-level cousin of the bottle-cap test's `{objectId, part}`); when null it is
  a free point in the setting.
- **"Random but contextual" = seeded variation per experience.** The setting `id`
  seeds the composition: the same experience re-renders the same setting
  deterministically; different experiences get contextual variety. Contextual, never
  arbitrary — `why` says in one sentence why this setting fits the question.
- **Additive & optional:** consumers that don't know `setting` ignore it; an AR client
  ignores it by law (the real room wins).

Implementation note: the staging composer (brain side) and the setting renderer
(Web backend) land in the same wave as this spec — see the per-file owners' code for
current state; this document is the contract they implement.

### 3.3 Placement intents — the design-system contract (built)

Placement is emitted as **intent, not coordinates**. The design system
(`studio/spatail/design_system.py`, encoding
`docs/spatail-placement-design-system.md`) resolves understanding + real asset
footprints + stream into the placement contract: `anchorType` / `anchorPreference`,
`scaleMode`, coverage, per-asset `footprints` with provenance
(`footprintSource: "library" | "object_size_llm" | "default_guess"`), and a
non-empty human-readable `decisionTrace` naming which hint/threshold fired at each
step. The `sceneContract` projection embeds the whole block as
`placement.designSystem`.

Each backend then solves intent → coordinates against **its** context (the setting
law, §3.2): the iOS `PlacementSolver` against the live `RoomModel`/ObjectRegistry; the
Web backend's `resolveLayout` against its synthetic room or a composed setting. Both
report what they actually did via `POST /placement-report`, and `/traces/view` puts
Brain → Contract → Client → Asset side by side so a bad placement lights up the guilty
column.

### 3.4 Trigger VM — `logic.triggers`, event → action (built brain-side; runtime split)

The brain emits discrete interaction logic as data:
`{"when": {"event", "target", "params"}, "do": [{"action", ...}]}` — carried at the
modular contract's top level (`triggers[]`, `studio/director/composer.py::_triggers`)
and inside the logic block (`logic.triggers`, `studio/director/logic.py::build_logic`).
Today's emitted event vocabulary is `onTap` / `onApproach` / `onGaze` (with
`radius_m` / `dwell_s` params) and actions `advance` / `playClip` / `label` /
`mechanic`.

The general form of this VM is the Swift core's **`RuleSystem`** (built, tested):
`when (event) → if (conditions: path op value) → do (actions)`, with the closed action
vocabulary (`setState`, `damage`, `spawn`, `despawn`, `playClip`, `applyEffect`,
`advanceStep`, `completeObjective`, `emitSignal`, `speak`, `win`, `lose`, `hud`, …)
and live `"$path"` argument resolution. Kit baseline rules and contract-authored rules
run through the same system. The modular `triggers[]` are the v0.5/0.6 dialect of the
same idea; mapping them onto `RuleSystem` rules in the app host, and executing them at
all on the Web backend, are convergence duties (§2.2) **(direction)**.

### 3.5 Aliveness / nature runtime — existence, not motion (built on web)

An object is alive by being authentically **what it is, in context** — stillness is as
alive as motion. The closed nature vocabulary:

`still · mechanical · vital · pendular · celestial · organic · creature`

- `still` — products, furniture, architecture: quiet grounded presence; turns to
  present itself when regarded.
- `mechanical` — engines, machines: runs (baked clip, else a working turn).
- `vital` — hearts, lungs: a real double-thump pulse, not a sine.
- `pendular` — pendulums, metronomes: swings; that is its nature.
- `celestial` — planets, stars: a majestic turn and steady radiance.
- `organic` — plants, cells: sway and slow growth.
- `creature` — animals, characters: breath, bob, and noticing you.

Implemented in the Web backend as `natureFor` (classification) + `updateLife`
(per-frame expression, attention response, isolation quieting) **(built, verified:
chair/TV→still, heart→vital, pendulum→pendular, planet→celestial, V8→mechanical)**.
The classifier is the on-device stand-in: **the proper home is the brain** — the
contract's `understanding` should emit the nature so runtimes just express it
**(direction)**. Baked clips are a thing's nature (an engine runs) and auto-play
unless the nature is stillness. The Swift backend expresses natures through the same
command vocabulary (`playClip`, `applyEffect`); a dedicated nature system in the Swift
core is a convergence duty **(direction)**.

### 3.6 Materialization — spawn-in as the loading state (built)

Matter appears; it does not pop. The materialization contract:

- A loading/generating asset occupies its **real reserved bounds** from the start
  (footprint + pose), so layout and placement are truthful before the mesh exists.
- The visual is a coalescing particle/voxel field at those bounds (`spawnIn` in the
  Web backend), replaced by the real GLB on arrival (generation-job poll + hot-swap,
  `/jobs/{id}`).
- **Never a fake solid object.** A pending element (setting element or asset) renders
  as a clearly-labeled materializing field — the user always knows what is real, what
  is arriving, and what is unavailable (§3.2 asset states).

---

## 4. Growth model — additive, closed, in lockstep

1. **Vocabularies only grow.** New capability = a new value in a closed vocabulary or
   a new additive contract block (like `setting`). Existing values never change
   meaning. Contracts are versioned (`0.5.0-spatail` → `0.8-spatail-engine`), and
   decoding stays tolerant so old payloads run on new engines.
2. **Kits land in lockstep with brain emissions** (§3.1). The trigger for engine work
   is always the brain learning a new emission — never speculative engine features.
3. **Both backends implement or explicitly degrade.** When a vocabulary grows, each
   backend either executes the new value or visibly degrades (labelled stub, identify
   overlay warning, `placeholderFor` marker). Silent dropping is a spec violation.
4. **Verification travels with growth.** A new emission ships with its headless test
   (Swift core) and/or its trace/report leg (`decisionTrace`, `/placement-report`),
   so the attribution page can always assign blame.

---

## 5. Why not Unity / Unreal

The obvious question deserves a plain answer:

- **Runtime-authored data vs design-time authoring.** Unity/Unreal are tools where
  humans author scenes and behaviour at design time and ship a build. SPATAIL's
  experiences are authored **at runtime by a model**, per question, as data. What we
  need is a small, inspectable VM for closed-vocabulary contracts — not an editor, an
  asset pipeline GUI, and a scripting surface a model would have to write code against
  (the brain never emits code, §1).
- **Embeddability.** The engine must live *inside* an AR iOS app (RealityKit owns the
  ECS/physics/renderer there — the adapter never reimplements them) *and inside a
  browser tab* (Three.js/WebXR). A Unity runtime embedded in both is heavyweight,
  license-encumbered, and still wouldn't share our contract semantics; the Swift core
  compiles into the app as a SwiftPM package, and the web runtime is one JS file.
- **Contract portability is the product.** The IP is the contract + the closed
  vocabularies + the placement intelligence — one emission renders on PC, phone, and
  headset. Porting SPATAIL to a new platform is implementing `SceneBackend` (one
  protocol), not porting a Unity project.
- **Honest scope.** We explicitly do not rebuild what engines are good at: no
  networking, persistence, IK, blend trees, character controllers, spatial audio —
  until a committed genre needs one. The platform's native engine (RealityKit,
  Three.js) does the heavy continuous simulation; our engine owns meaning, rules,
  goals, staging, and aliveness.

---

## 6. ML posture

The engine's relationship to machine learning, stated so we neither under-use ML nor
cargo-cult it.

### 6.1 Already ML (built)

- **VLM identity + parts.** The live perception path: PC vision engine
  (`pipeline/server/spatail_vision_engine.py`, frames :8798 / debug :8799; Qwen2.5-VL
  via Ollama by default, NIM/vLLM swappable) and the Gemini detector
  (`webxr/live/detector_server.py`, `gemini-2.5-flash`) produce open-vocabulary
  *what* — identity, detections, and per-part boxes
  (`vision.identification` + `parts[]`, LIVE_BRAIN_SPEC §1.3). The
  latency law stands: the VLM owns WHAT (~1 Hz, may be ~1 s stale); ARKit owns WHERE
  and FORM (60 Hz, never stale).
- **On-device Vision/CoreML detection.** The app's perception module runs detection on
  a dedicated background queue (single-flight, drop-on-busy) feeding the
  ObjectRegistry's depth-sampled 3D hitboxes.
- **iOS 27 Create ML reference-object tracking.** Continuous object tracking on
  iPhone (`ARObjectAnchor: ARTrackable`, Create ML reference objects with `usdzFile`),
  gated behind `SPATAIL_IOS27_TRACKING`.

### 6.2 Next — built when the trigger fires (direction)

- **(a) Synthetic-data factory.** The Blender spine + the engine render **labeled
  scenes from our own GLB asset library** (`public/assets/spatail-library/**`) —
  randomized poses, lighting, settings (§3.2 makes settings composable data) — to
  train custom detectors for classes we care about. **Trigger:** a recurring object
  class the VLM misses or is too slow on. Until then, the open-vocabulary VLM is
  cheaper than owning a training loop.
- **(b) Learned placement ranker.** Train a ranker over the placement-trace corpus —
  every `/modular` contract, every fusion plan, every client `/placement-report`, and
  user corrections — to score candidate placements, sitting **behind** the rule-based
  design system as a re-ranker, never replacing the decisionTrace. **Trigger:** trace
  volume with real user corrections (the trace persistence and `/placement-report`
  legs are built; the model is not worth training on synthetic agreement).

### 6.3 Explicit non-goals for now

- **End-to-end learned placement.** No black-box model deciding where things go.
- **RL for experience composition or placement.**

**The reason:** the rule-based system is **inspectable via `decisionTrace` and it
works.** Every placement names the hint/threshold that fired; the attribution page can
assign any failure to Brain, Contract, Client, or Asset. A learned end-to-end system
would trade that accountability for opaque gains we have no evidence we need. When
data says otherwise (§6.2's triggers), we add ML **behind** the inspectable interface,
not instead of it.
