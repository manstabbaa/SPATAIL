"""Manual-ingest agent for usermanualXR.

Reads a user manual (plain text now; PDF text-extraction upstream later)
and returns a structured `manual_plan`:

  - product_kind      one-line product classification (feeds the matcher)
  - product_keywords  extra match terms pulled from the manual
  - title             a display title for the walkthrough
  - steps[]           ordered procedure steps, each with:
        n             1-based step number
        title         short imperative ("Mount the frame")
        instruction   the viewer-facing narration (1-2 sentences, plain)
        target_parts  free-text part names mentioned ("frame", "blades")
        action        normalized verb: identify | mount | connect | power_on
                      | verify | clean | remove | slide | press | rotate | none
        spec          optional hard fact ("12 V DC")
        warning       optional caution ("do not overtighten")

This is a Sonnet text pass (cheap). PDF→text happens in the caller.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env", override=True)
except ImportError:
    pass


SYSTEM_PROMPT = """You are a manual-ingest agent for an XR walkthrough system.
You read a product's user manual and turn it into a STRUCTURED PLAN that a
3D walkthrough director will stage against a 3D model of the product.

Return ONE JSON object, no fences, no prose:

{
  "product_kind": "<one line, e.g. 'axial cooling fan'>",
  "product_keywords": ["<terms that identify the product class>"],
  "title": "<short walkthrough title>",
  "steps": [
    {
      "n": 1,
      "title": "<short imperative, <= 5 words>",
      "instruction": "<1-2 plain sentences the viewer hears>",
      "target_parts": ["<free-text part names the step is about>"],
      "action": "identify|mount|connect|power_on|verify|clean|remove|slide|press|rotate|none",
      "spec": "<optional hard fact or empty>",
      "warning": "<optional caution or empty>"
    }
  ]
}

Rules:
- product_kind must be a clean product CLASS, not the model name. "axial
  cooling fan", not "AxiCool 60".
- Merge "in the box" / "know your parts" prose into an opening step with
  action "identify" if there isn't already an identify step.
- Keep steps in the manual's order. 4-8 steps is ideal; collapse trivial
  ones. Drop pure spec tables (capture key numbers into a step's `spec`).
- `action` is the dominant physical action of the step. If the step is
  about a moving part (rotor, piston, motor turning on), prefer the verb
  that implies motion (power_on, rotate, slide) so the director knows to
  animate it.
- target_parts are the literal nouns from the manual ("blades", "frame",
  "rotor", "screws"). The director resolves them to meshes later.
- instruction is what a narrator SAYS — calm, present tense, no numbering.
"""


def ingest_manual(manual_text: str, *, model: str | None = None) -> dict:
    """Run the ingest agent. Returns the manual_plan dict."""
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("anthropic SDK not installed") from e

    model = model or os.environ.get(
        "ENGINEEXPLAINER_INGEST_MODEL", "claude-sonnet-4-5")
    client = anthropic.Anthropic()

    user_msg = (
        "Here is the user manual text. Produce the structured plan JSON.\n\n"
        "----- MANUAL -----\n"
        f"{manual_text.strip()}\n"
        "----- END MANUAL -----\n"
    )
    resp = client.messages.create(
        model=model, max_tokens=2000, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    data = _parse_json(raw)

    # Normalize / defensive defaults
    data.setdefault("product_kind", "unknown product")
    data.setdefault("product_keywords", [])
    data.setdefault("title", "Product Walkthrough")
    steps = data.get("steps", []) or []
    for i, s in enumerate(steps, start=1):
        s.setdefault("n", i)
        s.setdefault("title", f"Step {i}")
        s.setdefault("instruction", "")
        s.setdefault("target_parts", [])
        s.setdefault("action", "none")
        s.setdefault("spec", "")
        s.setdefault("warning", "")
    data["steps"] = steps
    return data


def _parse_json(raw: str) -> dict:
    if raw.startswith("```"):
        for chunk in raw.split("```")[1::2]:
            chunk = chunk.lstrip()
            if chunk.startswith("json"):
                chunk = chunk[4:]
            try:
                return json.loads(chunk.strip())
            except json.JSONDecodeError:
                continue
    return json.loads(raw)
