# SPATAIL Experience Spec — contract (v0.1)

The **Experience Spec** is the "post-compile XR" payload: a JSON program the app
reads **live** to assemble an interactive education experience in the user's real
room. New experiences ship as Spec + USDZs — **no app recompile**.

```
PROMPT ─► DIRECTOR ─► experience.json (+ N USDZs) ─► RUNTIME reads it ─► XR in your room
         (server)     THIS CONTRACT                  (iOS now, visionOS later)
```

This file is the single source of truth both sides build to. iOS-first,
dual-target: the Spec is platform-neutral; each platform's runtime resolves
placement/mechanics with its own APIs (iOS ARKit today, visionOS later).

## Transport (reuses the existing job contract)
The Director is invoked through the existing job server
(`docs/generative_ar_contract.md`). A job whose result is an experience returns,
in `GET /jobs/{id}`:
```jsonc
{ "status": "done", "stage": "ready",
  "experience_url": "/artifacts/exp_ab12cd.json",   // THIS spec
  "usdz_base": "/artifacts/" }                        // station USDZs resolve here
```
The client downloads the experience JSON, then each station's `hero.usdz`
(relative to `usdz_base` or absolute).

---

## Top-level shape
```jsonc
{
  "spec_version": "0.1",
  "id": "exp_ab12cd",
  "title": "Newton's Three Laws of Motion",
  "subject": "physics",
  "prompt": "teach me newton's laws",          // verbatim user prompt
  "summary": "Three short demos, one per law.", // one line, shown on entry
  "narration_tone": "warm, curious, classroom",
  "placement": { … },                            // how the whole thing sits in the room
  "stations": [ … ]                              // ordered learning points
}
```

## `placement` — how the experience sits in the real room
Grounded in Apple HIG (docs/apple-visionos): keep content in the ~1.5 m comfort
bubble, world-anchored (never head-locked), centered in the field of view, "bring
the object to the user — don't make them move."
```jsonc
{
  "anchor": "table",            // table | floor | free  (real surface to sit on)
  "layout": "arc",              // arc | line | cluster  (how stations arrange)
  "comfort_radius_m": 1.4,      // stations within this radius of the user (<=1.5)
  "facing": "user",             // stations rotate to face the learner
  "spacing_min_m": 0.18,        // min gap between station footprints
  "guided": true                // true = one station active at a time (a walk-through)
}
```

## `stations[]` — ordered learning points (the "presentation")
Each station = one beat: a hero 3D object + written panels + interactive mechanics.
Rule of thumb the Director applies:
**better written → panel · better seen → hero/scene · better felt → mechanic.**
```jsonc
{
  "id": "st1",
  "order": 1,
  "title": "An object in motion stays in motion",
  "subtitle": "First Law — Inertia",
  "narration": "With nothing to slow it down, the puck keeps gliding…",
  "hero": {
    "usdz": "exp_ab12cd_st1.usdz",     // relative to usdz_base, or absolute URL
    "animation": "baked",               // baked | none
    "footprint_m": { "w": 0.6, "d": 0.3, "h": 0.3 },  // real size (metres)
    "scale_mode": "dynamic"             // dynamic (UI-legible) | fixed (true real size)
  },
  "panels": [ … ],
  "mechanics": [ … ]
}
```

## `panels[]` — spatial UI (the "written" half)
A panel is a 2D card placed in 3D next to its hero (RealityKit attachment +
BillboardComponent so it faces the learner; flat text, no depth — per HIG).
```jsonc
{
  "id": "p1",
  "kind": "fact",               // title | fact | data | caption | quiz
  "title": "Inertia",
  "body": "That tendency to keep doing what it's already doing is inertia.",
  "anchor": "above_hero",       // above_hero | beside_left | beside_right | below_hero
  "billboard": true,            // always face the user
  "reveal": "on_focus"          // on_focus | on_tap | always  (when it appears)
}
```

## `mechanics[]` — interchangeable interaction modules (the "felt" half)
Each mechanic = `{type, params}`. Write-once, runs on iOS + visionOS (shared
RealityKit: InputTargetComponent + CollisionComponent + gesture / PhysicsBody).
v0.1 ships four:

```jsonc
// 1. play_baked — play the hero's baked USDZ animation
{ "type": "play_baked", "params": { "trigger": "on_focus", "loop": true } }
//    trigger: on_focus | on_tap | auto

// 2. tap_reveal — tap the hero (or a hotspot) to reveal a panel / next step
{ "type": "tap_reveal", "params": { "target": "hero", "reveals": "p2" } }

// 3. grab_physics — pick up & toss the hero with real physics
{ "type": "grab_physics",
  "params": { "body": "rigid", "restitution": 0.4, "friction": 0.7, "mass_kg": 0.5 } }
//    body: rigid | bouncy | heavy | soft   (maps to PhysicsBody mode + material)

// 4. quiz_panel — a question with options; reveals feedback + advances
{ "type": "quiz_panel",
  "params": { "question": "Which law is this?", "options": ["First","Second","Third"],
              "answer_index": 0, "on_correct": "advance" } }
//    on_correct: advance | reveal:<panelId> | celebrate
```

A station with no mechanics is a pure display beat (hero + panels only). The
Director must not invent mechanic types outside this set in v0.1; new types bump
the `spec_version`.

---

## Validation rules (enforced server-side by experience_spec.py)
- `spec_version` present and supported; `id`, `title`, ≥1 station.
- station `order` values are unique and 1..N.
- every `hero.usdz` is a non-empty path; `footprint_m` dims > 0.
- every panel `kind` ∈ enum; `quiz` panels need question+options+answer_index.
- every mechanic `type` ∈ {play_baked, tap_reveal, grab_physics, quiz_panel};
  params valid for the type; `tap_reveal.reveals` and `reveal:<id>` reference real panel ids.
- placement enums valid; `comfort_radius_m` ≤ 1.5.

## Versioning
Bump `spec_version` on any breaking change; keep additive where possible. Both
the Director and the iOS/visionOS runtime check it on load and refuse mismatched
majors.
