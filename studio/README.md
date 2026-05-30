# SPATAIL Educator — the Studio

Ask a question → get a 3D demo **modeled and animated in Blender**, **staged into a
room-scale tester "studio" by XR comfort rules**, and played in the browser today
(the same contract will drive the real XR runtime tomorrow).

This is the clean core after the pivot. It is built like a small game studio:

```
        you ask a question (text [+ image])
                       │
            ┌──────────▼───────────┐
            │  Director (curriculum)│   storyboard: ordered beats, each naming a
            │  .claude/agents       │   real-world DEMO + its physics params
            └──────────┬───────────┘
                       │ studio/scenes/<id>.json
            ┌──────────▼───────────┐
            │  Senior 3D Artist     │   MODELS recognizable real-world objects in
            │  (Blender)            │   Blender + bakes correct physics to keyframes
            └──────────┬───────────┘   studio/out/studio.glb  (+ metadata sidecar)
                       │
            ┌──────────▼───────────┐
            │  Senior Developer     │   PLACES every exhibit by XR rule (comfort
            │  (staging)            │   cone, focal plane, gaze-down baseline)
            └──────────┬───────────┘   studio/out/StudioSceneContract.json
                       │
            ┌──────────▼───────────┐
            │  tester room (viewer) │   plays it; toggle the XR comfort guides
            └───────────────────────┘   → later: visionOS / Magic Leap runtime
```

## Two hard rules
1. **Blender is the only engine.** All geometry is modeled in headless Blender.
2. **No primitive fallback, ever.** A puck on an *air-hockey table*, not a cylinder
   on a slab. An unknown demo aborts the build loudly — it never ships a grey box.

## Run it

```bash
# ask anything (CLI)
python studio/educator.py "How do Newton's laws of motion work?"

# tester room
python studio/viewer/server.py
# open http://localhost:5180/studio/viewer/studio.html  — or just use the
# "Ask anything" bar in the header to build a new demo from the browser.

# run a single scene directly
python studio/run.py --brief studio/brief/newtons_laws.json
```

Blender path is read from `BLENDER_EXE` (default
`C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`).

## Layout
| Path | Role |
|---|---|
| `studio/xr_design.py` | **The placement brain.** Apple visionOS + Magic Leap comfort constants + helpers. Single source of truth for "where content goes." |
| `studio/brief/*.json` | Inputs — a question + learning goal. |
| `studio/scenes/*.json` | Storyboards — ordered beats (Director's output). |
| `studio/blender/realworld.py` | **Library of real-world object builders** (table, ramp, ball, lab cart, coil spring, materials). Grows per question. |
| `studio/blender/build_studio.py` | Artist+staging: composes objects, bakes physics, exports GLB + metadata. |
| `studio/contract.py` | Developer handoff: emits `StudioSceneContract.json`. |
| `studio/run.py` | Pipeline orchestrator (build → contract). |
| `studio/educator.py` | The "ask anything" front door. |
| `studio/director_fixtures.py` | Offline question→scene routing for known topics. |
| `studio/viewer/` | The tester room (Three.js) + static server with `/api/ask`. |
| `studio/out/` | Build artifacts (GLB, metadata, contract). |

## The live agent team
The four roles in `.claude/agents/` (`studio-director`, `senior-3d-artist`,
`senior-developer`, `studio-qa`) are a real Claude Code agent team — enabled by
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in `.claude/settings.json`. Run them as a
team so the Artist and Developer can talk mid-build (e.g. the Developer measures a
footprint too wide for the comfort cone and asks the Artist to slim the geometry,
rather than shoving it out of view). Ask Claude:

> "Create an agent team — studio-director, senior-3d-artist, senior-developer,
>  studio-qa — to build a spatial demo for: <your question>. The Director writes
>  the scene spec, the Artist models real objects in Blender + animates the
>  physics, the Developer stages by xr_design and emits the contract, QA gates on
>  the two hard rules and the comfort budget. They message each other to converge."

## XR design sources encoded in `xr_design.py`
Apple visionOS Human Interface Guidelines (Immersive Experiences, Spatial Layout,
Eyes/Ergonomics) and Magic Leap "Designing for Excellence" / developer comfort &
content-placement docs. Key numbers: near-clip 0.37 m, focal plane 0.74 m, reading
band 1.0–1.5 m, far 10 m, 30° no-head-turn cone, 12° gaze-down baseline, reach
0.40–0.75 m, 60 pt min target, 90 fps.
```
