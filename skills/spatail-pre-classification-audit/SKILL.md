---
name: spatail-pre-classification-audit
description: Gate between treatment and classification — render 4 orthographic thumbnails of the treated asset and ask a vision LLM whether the user's prompt actually describes this geometry. Catches "user asked about an F1 wheel but uploaded a Mercedes dashboard CAD" before the engine/wheel/whatever classifier wastes work on the wrong asset and produces nonsense labels.
when_to_use: Right after spatail-treat-mesh finishes and before any asset-type-specific classifier (spatail-classify-engine, future spatail-classify-wheel, etc.) runs. Also run it again whenever a user re-prompts an existing asset with a meaningfully different topic ("wait, this is actually a transmission, not an engine").
---

# Pre-Classification Audit

## What this exists for

`spatail-treat-mesh` is asset-agnostic — it happily produces a clean treatment manifest for *any* CAD. The classifier downstream is **not** asset-agnostic — `spatail-classify-engine` will dutifully try to find crank throws in a chair. It will fail, but loudly and slowly, and the failure mode looks like "the classifier is buggy" rather than "wrong asset for this prompt".

The audit is the cheap, early check that prevents that whole cascade:

> *Does what the user is asking about plausibly match what we're actually looking at?*

One vision-LLM call, one verdict. If the verdict is **mismatch**, downstream stops and the user gets asked a clarifying question — they uploaded the wrong file, or they meant a different mechanism, or the prompt was about a feature that isn't visible.

## Where it sits in the pipeline

```
treat-mesh ──► [pre-classification-audit] ──► classify-* ──► measure ──► rig ──► ...
                       │
                       └── verdict=mismatch ──► ABORT, surface to user
                       └── verdict=unsure   ──► flag, proceed
                       └── verdict=match    ──► proceed silently
```

It is the **only** stage that consults the user's prompt text. Everything downstream is mesh/JSON-only and stays that way.

## Inputs

1. **`treatment.json`** — produced by `spatail-treat-mesh`. We pull from it:
   - `summary.bbox` — overall extents (cm-ish)
   - `summary.part_count` — how many segmented parts
   - `summary.shape_class_histogram` — `{blob: 5, rod-like: 20, disc-like: 10, ...}`
   - `summary.assetId`
2. **`prompt_text`** — the raw user message that triggered this run. *Not* a parsed/structured version — the full text, so the vision LLM can read the user's actual intent.
3. **`vision_callback`** — a function `(image_path, question_text) -> dict` injected by the SPATAIL agent. Audit does NOT bake in a specific vision model; it asks the caller to do the call. Returns `{verdict, reasoning, suggested_reprompt?}`.

The asset's treated `.blend` scene is assumed to be the currently loaded Blender scene (audit runs inside Blender so it can render).

## Audit steps

### 1. Compute framing target
- Read `bbox` from treatment manifest.
- Target = bbox centre. Size = half the longest bbox extent + 10% padding.

### 2. Render 4 thumbnails
Reuse `spatail_multiview_render` to produce a 2×2 grid PNG:
- Perspective (3/4 view)
- Front (orthographic, −Y)
- Right (orthographic, +X)
- Top (orthographic, +Z)

Save to `<asset>/audit/thumbnails.png`. Also save the 4 individual tiles in case the vision callback prefers separate images.

### 3. Build the audit question

Compose a question for the vision LLM that includes:
- Tile labels (so the LLM knows which view is which)
- Asset-level facts from the treatment manifest (part count, shape histogram, bbox size)
- The user's prompt text, verbatim
- A short instruction: "Does the user's prompt describe this object? Answer with one of: confident_match, likely_match, mismatch, unsure. Then give a one-sentence reasoning. If mismatch, suggest what the user probably meant by their prompt OR what this object actually looks like."

Pass `(thumbnails.png, question)` to `vision_callback`.

### 4. Score
The callback returns one of:
- **`confident_match`** — visual evidence clearly supports the prompt (e.g. prompt says "V10 engine", image obviously shows banks of pistons + crankshaft).
- **`likely_match`** — plausible but not certain (e.g. prompt says "steering wheel", image shows a ring with spokes).
- **`mismatch`** — visual evidence clearly contradicts (e.g. prompt about F1 steering wheel, image shows a flat dashboard).
- **`unsure`** — ambiguous geometry (e.g. partial CAD, only a few parts visible, vision LLM genuinely can't tell).

### 5. Write audit JSON

`<asset>/audit/<asset>.audit.json`:

```json
{
  "assetId": "v10_engine",
  "schemaVersion": "0.1.0-spatail-pre-classification-audit",
  "auditedAt": "ISO-8601",
  "inputs": {
    "treatment_json": "path/to/v10_engine.treatment.json",
    "prompt_text": "what do all the buttons on an F1 steering wheel do?"
  },
  "asset_facts": {
    "bbox_cm": [35.1, 30.4, 62.7],
    "part_count": 34,
    "shape_histogram": {"blob": 5, "rod-like": 20, "disc-like": 10}
  },
  "thumbnails_png": "path/to/audit/thumbnails.png",
  "tiles": {
    "persp": "...png", "front": "...png", "right": "...png", "top": "...png"
  },
  "verdict": "mismatch",
  "reasoning": "The prompt asks about buttons on an F1 steering wheel, but the rendered geometry shows a V-banked piston engine assembly (crankshaft, conrods, pistons). No steering wheel features are present.",
  "suggested_reprompt": "Did you mean to upload a steering-wheel CAD? The current asset looks like a V10 engine — would you like to ask about engine components instead?"
}
```

## Decision policy (consumed by the SPATAIL orchestrator)

| Verdict | Orchestrator action |
|---|---|
| `confident_match` | Proceed to classifier silently. |
| `likely_match` | Proceed; log a note. No user interruption. |
| `unsure` | Proceed, but surface a soft warning to the user: "I'm not 100% sure this CAD matches your prompt — proceeding anyway, ping me if labels look wrong." |
| `mismatch` | **Abort** downstream pipeline. Show the user `suggested_reprompt` and the thumbnails grid. Ask them to clarify or upload the right file. |

This policy lives at the orchestrator layer, not in the skill. The skill produces the verdict; the agent decides what to do with it.

## How to invoke

```python
exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_audit.py").read())

def my_vision_callback(image_path, question):
    # Caller wires this to whatever multimodal model they're using.
    return {"verdict": "mismatch",
            "reasoning": "...",
            "suggested_reprompt": "..."}

result = audit_asset_vs_prompt(
    treatment_json=r"C:/.../v10_engine.treatment.json",
    prompt_text="what do all the buttons on an F1 steering wheel do?",
    vision_callback=my_vision_callback,
    out_dir=r"C:/.../audit",
)
# result is the dict; same content also written to <out_dir>/<asset>.audit.json
```

If `vision_callback=None`, the skill still renders the thumbnails and writes the audit JSON with `verdict: "no_vision_callback"` so the orchestrator can ask the user directly using the image.

## Anti-goals (what this skill does NOT do)

- ❌ Be clever about ambiguity. If the user says "axe" and the CAD looks like a hammer — flag it as mismatch and let the user decide. We are not in the business of arbitrating fine semantic distinctions.
- ❌ Auto-rename the asset. The treatment manifest's `assetId` is sacred; we never overwrite it based on what the vision LLM thinks the object is. We only *report* the discrepancy.
- ❌ Re-segment, re-treat, or modify mesh. This is a read-only audit. If the treatment was bad, that's a `treat-mesh` problem.
- ❌ Make the verdict call ourselves. We always defer to `vision_callback`. The skill is plumbing + framing; the actual visual judgment is the caller's vision model.
- ❌ Try to handle prompts that aren't about the asset (e.g. "what's the weather"). The orchestrator filters those before they ever reach this skill.
- ❌ Cache or memoize. Each run is fresh. Re-audit on every new prompt that touches the asset — prompts change, audits change.

## Failure modes worth knowing about

- **Empty scene**: if treatment ran but nothing is currently loaded, thumbnails will be blank. The skill checks `bpy.data.objects` length pre-render and aborts with `verdict: "scene_empty"`.
- **Pillow missing**: `multiview_render` falls back to a manifest .txt instead of a composite PNG. The audit then asks the vision callback to consume the 4 tiles individually.
- **Vision callback raises**: the audit JSON gets `verdict: "callback_error"` and the error message in `reasoning`. Orchestrator should treat that as `unsure`.

## Why this earns its slot

Without this gate, the failure mode of "wrong CAD for the prompt" only surfaces ~3-4 stages downstream as garbled labels in the classifier or a rig that won't animate. By then we've burned compute and the user is confused about which layer is broken. One vision call up front turns that whole class of bug into a clean "did you mean to upload a different file?" question — at the cheapest possible point in the pipeline.
