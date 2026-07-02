# SpatailEngine

A **platform-agnostic, genre-agnostic spatial-experience engine**. The brain (server
director) authors *what was asked for* as data — entities, components, **rules/laws,
objectives/goals** — and the engine runs it. An explainer and a third-person room
shooter are the *same engine* with different systems and different data.

> Status: foundation / P0–P1 + the rules+objectives core. Builds in Xcode / SwiftPM on
> a Mac. The pure core has no platform dependencies and is covered by `swift test`.
> The RealityKit adapter is the integration seam (needs Xcode + a little app wiring).

---

## The one idea

The core is a **pure simulation**. It never imports RealityKit/ARKit/Metal/SwiftUI.
It talks to any platform across a single seam:

```
            EngineEvent  ──►  ┌──────────────────────┐  ──►  BackendCommand
 (tap, fire, collision,      │   SpatailEngineCore   │      (createVisual, playClip,
  raycast, frame, signals)   │   pure Swift sim      │       applyEffect, addPhysics,
            ◄──              └──────────────────────┘       applyImpulse, anchor, ...)
                                       ▲
                                       │ implements SceneBackend
                              ┌────────┴─────────┐
                              │ RealityKitBackend │  (iOS/visionOS — swap to port)
                              └──────────────────┘
```

**The declarative/imperative line never moves.** Continuous numerical simulation
(physics, projectile trajectories, collision, the 60 Hz tick, clip sampling, AR
tracking) is **native code** — a `System` or the backend. Structure, thresholds,
which-system, and discrete *when X happens do Y* reactions are **data the brain emits**.
That's how one engine honestly runs a declarative explainer and a real-time shooter.

---

## Layers

| Layer | Where | What |
|------|-------|------|
| **L1 Substrate** | `World`, `Entity`, `Component`, `Value`, `Math` | ECS-lite state + the blackboard + path resolution. Pure. |
| **L2 Systems** | `RuleSystem`, `ObjectiveSystem`, `HealthSystem`, `LifetimeSystem`, `SpawnerSystem`, `MoverSystem`, `StepSequencerSystem` | The only per-frame/native code. |
| **L3 Kits** | `ExplainerKit`, `ShooterKit` (`Kit` + `KitRegistry`) | A genre = an ordered system list + baseline rules/objectives + a param schema. |
| **L4 Brain** | server director (out of repo) → `ExperienceContract` | Picks a kit, fills params, emits entities + rules + objectives. |

Add a genre = add a `Kit` (and any new `System`s it needs). You never edit the engine
core, and there is no `if genre ==` anywhere.

---

## Rules, laws, objectives, goals — as data

A **rule** is `when (event) → if (conditions) → do (actions)`. The vocabulary is
closed and typed — **no expression language, no loops, no parser** (deliberately; see
"What this is not"). A **condition** is `path op value`. An **objective** is a set of
success/failure predicates over world state; the `ObjectiveSystem` evaluates them every
tick and publishes progress so a rule like *all objectives complete → win* can react.

```jsonc
// shoot a target → score; reach the target score → win   (ShooterKit ships these)
{ "id": "hit", "when": { "event": "collision" },
  "if": [ { "lhs": "self.tag.projectile", "op": "eq", "rhs": true },
          { "lhs": "other.tag.target",    "op": "eq", "rhs": true } ],
  "do": [ { "kind": "damage",  "args": { "target": "other", "amount": "$self.projectile.damage" } },
          { "kind": "despawn", "args": { "target": "self" } } ] }
```

Paths the brain can read/write (closed + total): `blackboard.<key>`, `param.<key>`,
`time`, `event.<key>`, and `self|target|other` → `<component>.<field>` /
`transform.position.x|y|z` / `tag.<name>` / `name` / `id`. An arg of the form
`"$<path>"` is resolved live; anything else is a literal.

Action kinds: `setState`, `addState`/`addScore`, `damage`, `heal`, `spawn`, `despawn`,
`setComponentField`, `playClip`, `applyEffect`, `advanceStep`, `setStep`,
`completeObjective`, `failObjective`, `emitSignal`, `speak`, `playSound`, `raycast`,
`win`, `lose`, `hud`. Unknown kinds are ignored (forward-compatible).

---

## Build & verify

```bash
cd ios/SpatailEngine
swift build              # builds SpatailEngineCore (+ RealityKit target on a Mac)
swift test               # runs the headless engine tests — this is your verification
```

`Tests/SpatailEngineCoreTests/EngineTests.swift` proves the thesis with no device:
a shooter contract runs **fire → projectile → collision → damage → death → score →
objective → win**; an explainer advances steps; contract-authored rules+objective drive
a collect-2-to-win loop.

---

## Integrate with the app (`ios/Spatail`)

1. `project.yml` already declares the package dependency (`SpatailEngineCore`,
   `SpatailEngineRealityKit`); run `xcodegen generate`.
2. In the AR layer, own one engine + one backend:

```swift
let backend = RealityKitBackend()
backend.resolveAssetURL = { GenerativeClient.localFileURL(for: $0) }   // reuse the existing downloader
backend.onSpeak = { Narrator.shared.say($0) }
anchorEntity.addChild(backend.root)

let engine = SpatailEngine(backend: backend)
backend.emit = { [weak engine] in engine?.submit($0) }                  // backend → engine
_ = backend.attachCollisions(to: arView.scene)                          // collisions → rules

engine.load(try ExperienceContract.decode(serverJSON))                  // from POST /modular (v0.8)
// per ARSession frame:
engine.tick(frame.timeInterval)
// taps / fire:
backend.reportTap(on: arView.entity(at: point))
engine.submit(EngineEvent(Ev.fire, payload: ["origin": cameraRay.originList, "impulse": cameraRay.impulseList]))
```

`ModularRuntime` becomes a thin host around this. The existing `SpatailMaterials`,
`PlacementSolver`, `RoomModel`, `ObjectAnchoringController` are reused by the backend
(they are already pure / separable). The minimal effect impl here is a placeholder —
fold `SpatailMaterials` into `applyEffect` when the adapter moves into the app target.

---

## Server contract

The director emits `ExperienceContract` JSON (schema `0.8-spatail-engine`). It is a
strict superset of today's modular contract, so a v0.6/0.7 payload still decodes
(every field defaults; `sequence` maps to `steps`, `assets` carry through). See
`Samples/sample_explainer.json` and `Samples/sample_shooter.json`.

The two-stage director job: (1) generic understanding → pick `genre`/`kit`; (2) emit
that kit's validated params + entities + a closed-vocabulary rule/objective table.

---

## What this is *not* (deferred, on purpose)

For a small team, the trap is rebuilding Unity. Explicitly **not built**:

- **No expression VM / scripting language.** Conditions are `path op value`. If a real
  third genre proves the closed form insufficient, add a single typed predicate — not a
  grammar.
- **No generic JSON→RealityKit component decoder.** The backend maps a known visual/
  physics subset; new *core* component types are free (data), new *rendered* ones are a
  backend change.
- **No networking, persistence, character controller, IK, blend trees, spatial audio.**
  Add only when a committed genre needs it.
- RealityKit **is** the ECS/physics/renderer — the adapter never reimplements an entity
  store, physics integrator, or scheduler.
