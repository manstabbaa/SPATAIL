# EngineExplainer

Agentic AI-driven interactive car repair self-service. User asks a question; the system explains the mechanical answer **spatially** — animating the actual parts of a 3D engine, highlighting what matters, hiding what doesn't, sequencing the explanation as visual storytelling.

Built around a **V8 engine** asset; designed to generalise to any rigged mechanism downstream.

---

## The five layers

```
┌──────────────────────────────────────────────────────────────────┐
│  USER  ── "How does the valvetrain time itself with the crank?"  │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────┐
│  1. INTELLIGENCE (Python, server)           │   intelligence/
│     prompt → context build → mechanic → ────┤
│     director → critic → spatial-contract    │
└─────────────────────────────────────────────┘
                              │
                              ▼  contract.json (wire format)
┌─────────────────────────────────────────────┐
│  2. RUNTIME (Three.js, browser)             │   web/
│     loads GLB, plays contract beats         │
│     drives both 3D scene AND HTML UI        │
└─────────────────────────────────────────────┘
                              │
                              ▼  reads
┌─────────────────────────────────────────────┐
│  3. AUTHORED ASSETS                         │   engine/
│     - v8_engine.glb (rigged, animated)      │
│     - part_registry.json (stable IDs)       │
│     - animation_library.json (named acts)   │
└─────────────────────────────────────────────┘
                              ▲
                              │  produced by
┌─────────────────────────────────────────────┐
│  4. AUTHORING (Blender + MCP)               │   authoring/
│     treat → classify → rig → animate →      │
│     export GLB + registry                   │
└─────────────────────────────────────────────┘
                              │
                              ▼  uses
┌─────────────────────────────────────────────┐
│  5. SKILLS (reusable SPATAIL building       │   skills/
│     blocks). Asset-agnostic, headless.      │
└─────────────────────────────────────────────┘
```

---

## The spatial contract — the load-bearing abstraction

Everything passes through one wire format: a **spatial contract** (JSON). It declares *what the explanation looks like*, decoupled from *how it's rendered*.

The intelligence layer **writes** contracts. The runtime **plays** them. Neither knows about the other beyond this schema. That separation is what makes the intelligence platform-agnostic — same contract drives web today, Vision Pro / Quest / iOS AR later.

A contract has:

| Section | What it declares |
|---|---|
| `meta` | id, prompt, version, generated-by metadata |
| `explanation` | the prose answer (title, summary, narration script per beat) |
| `scene` | initial visibility (show/hide), camera framing, exploded-view state, time-of-day |
| `beats` | ordered list of timed steps; each beat has `actions` |
| `actions` | atomic UI/scene operations: `highlight`, `label`, `play_animation`, `move_camera`, `show_panel`, `hide`, `pulse`, `arrow`, `dim_others` |
| `assets` | references to part IDs (from `part_registry.json`) and animation IDs (from `animation_library.json`) |

Schema lives at [contracts/schema/spatial-contract.schema.json](contracts/schema/spatial-contract.schema.json).

---

## The authoring pipeline (Blender → GLB)

Authoring is a **one-time per asset** (V8, then future engines/mechanisms). Composes existing SPATAIL skills plus two new ones:

```
treat-mesh                       # generic loose-parts segmentation        (existing)
   │
   ▼
classify-engine                  # semantic roles: piston/rod/throw/pin    (existing)
   │
   ▼
rig-engine                       # kinematic chain + constraints           (existing)
   │
   ▼
spatail-part-animations          # NEW: per-part baked glTF animations
   │                             # (piston stroke, rod swing, crank rotation,
   │                              valve lift, cam rotation, etc.)
   ▼
spatail-author-contract          # NEW: writes part_registry.json,
   │                             # animation_library.json, exports GLB
   ▼
engine/v8_engine.glb             # the deliverable
engine/part_registry.json
engine/animation_library.json
```

`part_registry.json` is the stable directory of **what exists**. The agent reads this to know what it can address. Without it, the agent would have to introspect the GLB at runtime — fragile.

---

## The intelligence pipeline (agentic AI)

Four agents in series, each with one job:

1. **Context builder** (not an LLM call) — gathers everything the next agent needs into a single context window: the prompt, the part registry, the classification roles, the available animations, the camera framing presets, the user's recent question history, the current visible state of the scene.

2. **Mechanic** (LLM) — reads the question + context, writes the **technical answer** in plain language. No spatial reasoning yet. Just: *what is mechanically true*. System prompt frames it as a senior powertrain engineer who knows how to explain to a curious owner.

3. **Director** (LLM) — translates the mechanic's answer into a **storyboard of beats**. For each beat decides: what to show, what to hide, what to highlight, what to animate, where the camera should be, what UI panel (if any) supports the beat. Outputs a draft contract. System prompt heavily biased toward "use the 3D" — labels float on parts, animations replace bullet points, panels are last resort.

4. **Critic** (LLM, optional but cheap) — validates the contract: do all referenced part IDs exist? Do all animation IDs exist? Does the camera frame anything visible? Are beats reasonably timed? Returns either `OK` or `revise: <issue list>` → director re-runs.

Final contract is sent to the runtime.

System prompts live in `intelligence/prompts/`.

---

## The runtime (web, Three.js)

A static site. Loads the GLB once, holds it ready. Listens for a contract over a simple POST. Executes the contract's beats in order:

| Action type | What the runtime does |
|---|---|
| `highlight` | Apply an emissive indigo edge shader to the target part |
| `dim_others` | Mute material brightness on everything except the highlight set |
| `play_animation` | Play the named glTF action from `t0` to `t1` over `duration` |
| `move_camera` | Tween camera position + target with easing |
| `label` | Spawn an HTML PartLabel anchored to the part's world position |
| `show_panel` | Spawn an HTML ExplanationCard at screen anchor |
| `hide` | Set part visibility false |
| `arrow` | Spawn a 3D arrow primitive between two anchor points |
| `pulse` | Soft scale + emissive pulse (attention cue) |

**Vision Pro design biases baked in:**
- Labels are *attached to parts*, not in a side rail
- One panel max on screen at a time
- Translucent glass panels, never opaque
- Motion is gentle (250–600ms eases), no snappy UI animation
- 3D-first: when a choice exists between "label on a part" and "panel with text", we label

---

## Folder map

```
engineexplainer/
├── README.md                       (this file)
├── docs/
│   ├── ARCHITECTURE.md             (deeper dive)
│   ├── CONTRACT_GUIDE.md           (how to write/extend contracts)
│   └── AUTHORING_GUIDE.md          (Blender → GLB workflow)
│
├── engine/                         (3D asset outputs)
│   ├── v8_engine.glb
│   ├── part_registry.json
│   ├── animation_library.json
│   └── parts/                      (per-part isolated previews, optional)
│
├── contracts/
│   ├── schema/
│   │   └── spatial-contract.schema.json
│   └── examples/                   (hand-authored examples for testing the runtime)
│       ├── how-does-a-piston-work.json
│       ├── how-does-the-crank-turn.json
│       └── what-is-valve-timing.json
│
├── authoring/                      (Blender-side scripts, run via MCP)
│   ├── author_v8.py                (composes the full pipeline for the V8)
│   ├── export_part_registry.py
│   └── export_animation_library.py
│
├── intelligence/
│   ├── orchestrator.py             (entry point: prompt → contract)
│   ├── context_builder.py
│   ├── agents/
│   │   ├── mechanic.py
│   │   ├── director.py
│   │   └── critic.py
│   ├── prompts/                    (system prompts as plain .md)
│   │   ├── mechanic.md
│   │   ├── director.md
│   │   └── critic.md
│   └── tools/                      (typed tool defs the director can emit)
│       └── contract_actions.py
│
├── web/
│   ├── index.html
│   ├── package.json
│   ├── public/
│   │   └── engine/                 (symlink or copy of ../engine/)
│   └── src/
│       ├── main.js                 (entry)
│       ├── viewer.js               (Three.js scene)
│       ├── contract_player.js      (executes beats)
│       ├── api.js                  (talks to intelligence/)
│       └── components/             (UI overlays)
│           ├── ExplanationCard.js
│           ├── PartLabel.js
│           └── PromptBar.js
│
└── skills/                         (new SPATAIL skills, follow same pattern as ../skills/)
    ├── spatail-author-contract/
    │   ├── SKILL.md
    │   └── pipeline.py
    ├── spatail-part-animations/
    │   ├── SKILL.md
    │   └── pipeline.py
    └── spatail-storyboard/
        ├── SKILL.md
        └── pipeline.py
```

---

## Build status & next steps

| Layer | Status |
|---|---|
| Folder scaffold | ✓ |
| Architecture doc | ✓ (this file) |
| Spatial contract schema | building now |
| `engine/v8_engine.glb` + registry | pending (re-author with rigger + animator) |
| Per-part animation library | pending |
| Web runtime | scaffold pending |
| Intelligence pipeline | scaffold pending |
| Thin slice end-to-end | pending |

---

## Design principle (the one to remember)

> **The 3D is the explanation. The HTML is the punctuation.**

If the explanation could be a recipe-app slideshow, it's not earning the spatial medium. Every beat should make the viewer say "I couldn't have understood that without seeing it move."
