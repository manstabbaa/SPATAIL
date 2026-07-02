"""Semantic validator — text-only Sonnet pass over a draft contract.

Catches the failure mode the visual validator caught after the fact: the
director picks parts that don't match what the narration says. We can't
detect that with a JSON schema — we need semantic reasoning over the
contract's actions + narration + the available hero parts.

This runs in the orchestrator's inner loop BEFORE the contract is shipped
to the runtime, so it's cheap (no screenshots, no Opus) and prevents the
worst class of failures.

The visual validator (visual_validator.py) is the post-hoc Opus-vision
ground truth. This semantic validator is the cheap inline gate.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env", override=True)
except ImportError:
    pass


SYSTEM_PROMPT = """You are a fast text-only QA reviewer for a 3D explainer
system. Given a draft "spatial contract" + the inventory of parts the runtime
can actually show, you predict whether playing the contract will visually
answer the user's question.

What you're looking for:

1. **Topic mismatch.** Narration says "the piston compresses fuel" but no
   piston_* / cylinder / combustion-region part appears in any beat's
   actions. The contract talks about something the asset can't show.

2. **Hidden-internals problem.** Narration discusses internal parts
   (pistons, rods, crankshaft) but the contract has done nothing to
   make them visible to the viewer. Any of these counts as "exposing":
     - `dim_others(except_=[internal_part_ids], factor>=0.40)` — the
       PREFERRED X-ray pattern; shell goes translucent while the
       highlighted internals show through (the CAD's piston geometry is
       just thin ring crowns, so removing the shell entirely makes them
       look like floating wireframe — keep the shell as silhouette)
     - `show_only(target=[piston_*, crank_throw_*, ...])` — cut-away
       pattern; hides everything except the listed parts
     - `hide(target=[engine_block, intake_top, valve_cover_*, exhaust_*])` —
       the older list-based shell-hide pattern
   Also count it as exposure if the beat just calls `highlight()` on an
   internal part without any visibility change (the highlight's emissive
   makes the part glow even through the shell).
   Flag this only if the beat narrates about internal parts AND none of
   the above appear in this beat or any earlier beat that's still in
   effect (no `reset(scope="visibility")` or `reset(scope="all")` since
   then). Note: `reset(scope="highlights")` does NOT touch visibility —
   never flag it as "hides internals".

3. **Tiny target, wide camera.** A beat uses `show_only` on small parts
   (pistons are ~3cm wide on this engine) but the camera is still on a
   wide preset like `hero_threequarter`. The result is a speck in the
   corner. Flag this if `show_only` appears without a `frame_on` or
   `move_camera(to_preset="cylinder_close")` in the same beat.

3. **Empty-frame beats.** A beat has narration but its actions don't
   include any highlight, label, animation, or visibility change on a
   part — viewer hears words while staring at an unchanged scene.

4. **Confused references.** A beat narrates about part X but `highlight`s
   part Y. Or labels part X with text describing Y.

5. **No anchor for arrows / panels.** An `arrow` action targets a part id
   that isn't visible in that beat (was never `show`n and was inside a
   hidden parent). Or a `show_panel` says "here is X" while X is off-camera.

What you should NOT flag:
- Color or styling choices
- Beat durations or pacing
- The actual mechanical correctness of the explanation (mechanic handled that)
- Soft critiques like "this could be better explained"

Reply with one JSON object — no fences, no prose:

{
  "verdict": "ok" | "revise",
  "issues": [
    {"beat": "<beat id>", "kind": "topic_mismatch" | "hidden_internals" | "tiny_target_wide_cam" | "empty_beat" | "confused_ref" | "no_anchor",
     "fix": "<one sentence telling the director what to change>"}
  ],
  "summary": "<one sentence on whether the contract will land>"
}

Be strict but actionable. Prefer fewer high-confidence issues over many
nitpicks. If everything looks plausible, return verdict: "ok" with empty issues.
"""


@dataclass
class SemanticIssue:
    beat: str
    kind: str
    fix: str


@dataclass
class SemanticReview:
    verdict: str
    issues: list[SemanticIssue]
    summary: str

    def is_ok(self) -> bool:
        return self.verdict == "ok" and not self.issues

    def to_director_brief(self) -> str:
        """Compact issue list to feed back to the director for revision."""
        if self.is_ok():
            return ""
        lines = ["The previous contract draft had these specific problems — fix them:"]
        for i in self.issues:
            lines.append(f"  - In beat '{i.beat}' ({i.kind}): {i.fix}")
        return "\n".join(lines)


def validate(contract: dict, registry: dict, prompt: str, *,
             model: str | None = None) -> SemanticReview:
    """Run the semantic validator. Cheap — Sonnet by default."""
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("anthropic SDK not installed") from e

    model = model or os.environ.get("ENGINEEXPLAINER_SEMANTIC_VALIDATOR_MODEL", "claude-sonnet-4-5")
    client = anthropic.Anthropic()

    # Compact view of what the runtime CAN show
    hero_parts = registry.get("hero_parts", {})
    aliases = registry.get("aliases", {})
    anatomy = registry.get("internal_anatomy", {})

    catalog = {
        "aliases": aliases,
        "hero_part_ids": list(hero_parts.keys()),
        "internal_anatomy": anatomy,
    }

    # Strip the contract down to just what the validator needs to see
    compact_contract = {
        "meta": {"prompt": contract.get("meta", {}).get("prompt", prompt)},
        "explanation": contract.get("explanation", {}),
        "beats": [
            {
                "id": b.get("id"),
                "narration": b.get("narration", ""),
                "actions": [
                    {k: v for k, v in a.items() if k in ("type", "target", "except", "animation", "component", "from", "to", "factor")}
                    for a in b.get("actions", [])
                ],
            }
            for b in contract.get("beats", [])
        ],
    }

    user_msg = (
        f"USER QUESTION:\n{prompt}\n\n"
        f"WHAT THE RUNTIME CAN SHOW:\n{json.dumps(catalog, indent=2)}\n\n"
        f"DRAFT CONTRACT:\n{json.dumps(compact_contract, indent=2)}\n\n"
        "Reply with the JSON described in the system prompt. Nothing else."
    )

    resp = client.messages.create(
        model=model, max_tokens=1500, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    if raw.startswith("```"):
        for chunk in raw.split("```")[1::2]:
            chunk = chunk.lstrip()
            if chunk.startswith("json"): chunk = chunk[4:]
            try: data = json.loads(chunk.strip()); break
            except json.JSONDecodeError: continue
    else:
        data = json.loads(raw)

    return SemanticReview(
        verdict=str(data.get("verdict", "revise")).lower(),
        issues=[SemanticIssue(beat=str(i.get("beat", "?")),
                              kind=str(i.get("kind", "unknown")),
                              fix=str(i.get("fix", "")))
                for i in data.get("issues", [])],
        summary=str(data.get("summary", "")),
    )
