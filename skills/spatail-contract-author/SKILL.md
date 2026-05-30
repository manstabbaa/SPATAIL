---
name: spatail-contract-author
description: Write a spatial-explanation contract for the SPATAIL/EngineExplainer runtime. Given a user question, an answer outline, an asset's part registry + animation library + director_hints, emit a sequence of ContractBuilder tool calls that drive the 3D viewer beat-by-beat. The output is a single fenced ```python block of `ctx.beat(...)` + action calls that the runtime executes against the live GLB. Use this skill whenever a new mode is added to the runtime (a new asset, a new action type, a new visual idiom) and the existing director.md prompt needs to be regenerated, or when authoring a contract by hand for testing.
when_to_use: When extending the EngineExplainer to a new asset and you need to either (a) update the director's system prompt with the new asset's quirks, or (b) hand-author a reference contract for that asset to seed the example library. Also use when reviewing whether the orchestrator's existing director prompt still encodes the right invariants (the hard rules below should match what's in intelligence/prompts/director.md).
---

# Contract Author

## What you're authoring

A **spatial contract** is a JSON document describing a sequence of "beats"
that play against a 3D asset. Each beat has:

  - `narration` — what the viewer hears
  - `duration` — how long the beat plays
  - `actions[]` — what happens visually during the beat

The runtime (`engineexplainer/web/`) consumes the contract and drives the
WebGL scene: highlights, dim_others, labels, halos, camera moves, baked
animations, panels.

A contract is **expressed in Python via a `ContractBuilder`** — you call
methods on a `ctx` variable and the orchestrator records each call into
the final JSON. Authoring in Python keeps the surface ergonomic and
catches typos at runtime.

## Hard rules — every contract MUST satisfy these

These are not preferences. Each rule encodes a documented failure mode
from a prior contract that looked broken on the web. They live in
`intelligence/prompts/director.md` for the live director agent — keep
both in sync.

1. **Beat 0 must not `dim_others` and must not `hide`.** The viewer's
   first frame must show the whole asset clearly. Use beat 0 to
   `move_camera` to the asset's hero preset and set the scene with
   narration only.

2. **`dim_others.factor` stays in [0.40, 0.65]** (or whatever range the
   asset's `director_hints` declare). Below that the asset goes
   invisible against the page; above it nothing is dimmed.

3. **Every beat must have at least one VISIBLE target.** After
   `dim_others`, the excepted parts must also be `highlight`-ed or
   `label`-ed so the viewer SEES the focus.

4. **Reference parts ONLY by alias or by an id present in the
   registry's `parts` map.** Asset GLBs typically have hundreds of
   bolts/brackets that aren't worth labelling — they're not in the
   registry and shouldn't be in your contract.

5. **`show_only` needs `frame_on` in the same beat.** Tiny parts at a
   wide camera = a speck in the corner. The pair is mandatory.

6. **`reset(scope="all")` + internal animations are mutually
   exclusive** in the same beat. Either reset+exterior or internals
   exposed+rotor animation — never both.

7. **One panel maximum at any time.**

8. **Camera moves are deliberate** — one `move_camera` per beat at
   most.

## Adapting to the asset — read `director_hints`

Every part registry includes a `director_hints` block. It is the
source of truth for asset-specific overrides:

```json
{
  "asset_kind": "small axial cooling fan",
  "scale": "60mm × 60mm × 10mm",
  "background_default": "#F5F4EF",
  "preferred_camera_presets": ["hero_front", "hero_threequarter"],
  "avoid_presets": ["section_side", "cylinder_close", "topdown"],
  "dim_others_max_factor": 0.6,
  "dim_others_min_factor": 0.45,
  "key_moves": ["the rotor spins (blade_spin)", ...],
  "narration_tone": "concise — this is a simple machine",
  "do_not": ["do NOT call show_only — there is no shell to hide", ...]
}
```

Specifically:

  - `ctx.scene(background=...)` MUST use `background_default`. The
    current shell is cream paper — dark backgrounds make the canvas
    a hole on the page.
  - Camera presets MUST come from `preferred_camera_presets` and never
    from `avoid_presets`.
  - `dim_others.factor` stays inside the asset's declared range.
  - Beat count should match `narration_tone` (concise → 4 beats,
    technical → 7).
  - The `do_not` list is absolute.

## Available tool calls

```python
ctx.scene(camera_preset="...", background="#F5F4EF")    # initial state
ctx.beat(id="...", narration="...", duration=5.0)
  ctx.highlight(target, color="#5046E5", intensity=1.0)
  ctx.dim_others(except_=[...], factor=0.5)
  ctx.show(target) / ctx.hide(target)                   # use sparingly
  ctx.show_only(target=[...])                           # cut-away pattern
  ctx.frame_on(target=[...], margin=1.8)
  ctx.play_animation(name, from_=0, to=1, rate=1.0, loop=False)
  ctx.move_camera(to_preset="hero_threequarter")
  ctx.label(target, text, kicker=None, anchor="auto")
  ctx.show_panel("ExplanationCard", title=..., body=...)
  ctx.hide_panel()
  ctx.arrow(from_=part_id_or_xyz, to=part_id_or_xyz, color="#5046E5")
  ctx.pulse(target, cycles=1)
  ctx.reset(scope="highlights" | "visibility" | "camera" | "all")
ctx.title = "..."
ctx.summary = "..."
```

## Output format

Reply with **one fenced `python` block** of tool calls against `ctx`.
Start with `ctx.scene(...)`, alternate `ctx.beat(...)` + action calls,
end with `ctx.title = "..."` and `ctx.summary = "..."`.

## When in doubt

Mute the narration mentally and ask: *would the viewer still see
meaningful change in the 3D scene at each beat?* If no, re-stage.

## Composition

```
mechanic → CONTRACT-AUTHOR (this skill, or the live director.md prompt)
        → critic (schema) → semantic-validator (content)
        → contract JSON → runtime
                              ↓
                       motion-validator (per asset bake)
```

## Anti-goals

- ❌ Hard-code asset-specific knowledge in the prompt. Put it in
  the asset's `director_hints` block instead.
- ❌ Author contracts that change the asset itself (bake animations,
  re-parent meshes). Those are Blender authoring concerns handled by
  `spatail-blender-director` + `spatail-animate-from-rest` and
  triggered via `ctx.bake_animation(...)` (which the orchestrator
  intercepts before shipping).
- ❌ Reach for `show_only` first. It's the last-resort cut-away. The
  default is `dim_others` x-ray (this CAD has thin geometry that
  reads as wireframe when isolated).
- ❌ Use Unicode dashes / minus signs / smart quotes in the Python
  block. The orchestrator normalises them but it's brittle.
