"""Visual validator — Opus-vision QA pass over a played contract.

Given a contract and a screenshot of each beat as it appeared on screen,
asks Opus to verify that the visual actually supports the narration:
  - Is the engine visible at all?
  - Is the part the narration talks about identifiable in frame?
  - Are floating UI labels anchored on something the viewer can see?
  - Does the camera frame the action, or is the subject off-screen?

Returns per-beat issues. The orchestrator can use this as a final gate
before shipping the contract to the runtime; it can also run as a
post-hoc QA pass against any contract+screenshots pair.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Load .env so this can be invoked standalone without server.py priming the env
try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env", override=True)
except ImportError:
    pass


VALIDATOR_PROMPT = """You are a QA reviewer for an interactive 3D explainer
about a V8 engine. The user asks a question, the system generates a multi-
beat spatial contract, and the runtime plays it in a Three.js viewer.

For each beat, you'll see ONE screenshot of the viewer as that beat plays,
plus the beat's narration text and the actions it declared.

Your job is to answer, per beat, three questions in JSON:

  1. visible: Is the engine itself clearly visible in the frame?
     (not just floating arrows/labels against a dark void)
  2. on_topic: Does the visual show what the narration is talking about?
     (e.g. if narration says "the piston", can the viewer plausibly see
      a piston-region in the frame?)
  3. ui_anchored: Are floating labels/panels positioned on or near
     visible 3D geometry (not in empty space)?

Then list the concrete problems you see, if any.

Reply with a single JSON object — no prose, no markdown fences:

{
  "verdict": "ok" | "issues",
  "visible": true | false,
  "on_topic": true | false,
  "ui_anchored": true | false,
  "issues": ["<short issue 1>", "<short issue 2>", ...],
  "summary": "<one sentence, what the viewer actually sees>"
}

Be specific in `issues`. Examples:
  - "engine is invisible — only arrows and labels visible against dark bg"
  - "narration mentions piston but no piston-area is in frame"
  - "label 'Crank throw' floats in empty space, no part beneath it"
  - "camera framing too tight — viewer can't tell which part is being shown"

Issues should be **fixable by re-staging the beat**, not nitpicks. Don't
flag color choices, font sizes, animation timing.
"""


@dataclass
class BeatReview:
    beat_id: str
    verdict: str            # "ok" | "issues"
    visible: bool
    on_topic: bool
    ui_anchored: bool
    issues: list[str]
    summary: str

    def is_ok(self) -> bool:
        return self.verdict == "ok" and not self.issues


@dataclass
class ContractReview:
    contract_id: str
    beat_reviews: list[BeatReview] = field(default_factory=list)

    def is_ok(self) -> bool:
        return all(b.is_ok() for b in self.beat_reviews)

    def to_text(self) -> str:
        # ASCII only — Windows default consoles (cp1252) choke on box-drawing
        # / checkmark glyphs and the whole report gets lost to a UnicodeEncodeError.
        lines = [f"=== Visual review of '{self.contract_id}' ==="]
        for b in self.beat_reviews:
            mark = "OK " if b.is_ok() else "BAD"
            lines.append(f"\n[{mark}] beat '{b.beat_id}'  visible={b.visible} on_topic={b.on_topic} ui_anchored={b.ui_anchored}")
            lines.append(f"    summary: {b.summary}")
            for iss in b.issues:
                lines.append(f"    - {iss}")
        lines.append("\n--- overall: " + ("OK - all beats pass" if self.is_ok() else "BAD - revisions needed"))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "contract_id": self.contract_id,
            "overall_ok": self.is_ok(),
            "beats": [
                {"beat_id": b.beat_id, "verdict": b.verdict, "visible": b.visible,
                 "on_topic": b.on_topic, "ui_anchored": b.ui_anchored,
                 "issues": b.issues, "summary": b.summary}
                for b in self.beat_reviews
            ],
        }


def _vision_call(image_bytes: bytes, beat_meta: dict, *, model: str | None = None) -> dict:
    """Single Opus-vision call. Returns a dict matching VALIDATOR_PROMPT's schema."""
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("anthropic SDK not installed") from e

    model = model or os.environ.get("ENGINEEXPLAINER_VALIDATOR_MODEL", "claude-opus-4-5")
    client = anthropic.Anthropic()

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    user_content = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        },
        {
            "type": "text",
            "text": (
                f"BEAT META:\n{json.dumps(beat_meta, indent=2)}\n\n"
                "Respond with the JSON schema described in the system prompt. Nothing else."
            ),
        },
    ]
    resp = client.messages.create(
        model=model,
        max_tokens=600,
        system=VALIDATOR_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    raw = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    # Tolerate fenced output
    if raw.startswith("```"):
        for chunk in raw.split("```")[1::2]:
            chunk = chunk.lstrip()
            if chunk.startswith("json"): chunk = chunk[4:]
            try: return json.loads(chunk.strip())
            except json.JSONDecodeError: continue
    return json.loads(raw)


def validate_beat(image_bytes: bytes, beat: dict, *, model: str | None = None) -> BeatReview:
    """Validate one beat against its rendered screenshot."""
    meta = {
        "id": beat.get("id"),
        "narration": beat.get("narration", ""),
        "duration_sec": beat.get("duration", 0),
        "actions": [
            {"type": a.get("type"),
             **({"target": a["target"]} if "target" in a else {}),
             **({"animation": a["animation"]} if "animation" in a else {}),
             **({"component": a["component"]} if "component" in a else {})}
            for a in beat.get("actions", [])
        ],
    }
    result = _vision_call(image_bytes, meta, model=model)
    return BeatReview(
        beat_id=str(beat.get("id")),
        verdict=str(result.get("verdict", "issues")).lower(),
        visible=bool(result.get("visible", False)),
        on_topic=bool(result.get("on_topic", False)),
        ui_anchored=bool(result.get("ui_anchored", False)),
        issues=list(result.get("issues", [])),
        summary=str(result.get("summary", "")),
    )


def validate_contract(contract: dict, screenshots_by_beat: dict[str, bytes],
                      *, model: str | None = None) -> ContractReview:
    """Validate every beat that has a screenshot. Missing screenshots get a
    placeholder review noting they weren't captured."""
    review = ContractReview(contract_id=str(contract.get("meta", {}).get("id", "?")))
    for beat in contract.get("beats", []):
        bid = beat.get("id")
        if bid in screenshots_by_beat:
            review.beat_reviews.append(validate_beat(screenshots_by_beat[bid], beat, model=model))
        else:
            review.beat_reviews.append(BeatReview(
                beat_id=str(bid), verdict="issues", visible=False,
                on_topic=False, ui_anchored=False,
                issues=["screenshot not captured for this beat"],
                summary="(no screenshot)",
            ))
    return review


# -----------------------------------------------------------------------------
# CLI: validate a saved contract + screenshots folder
# -----------------------------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        print("usage: python visual_validator.py <contract.json> <screenshots_dir>")
        print("  screenshots_dir should contain <beat_id>.png for each beat to review")
        sys.exit(1)
    contract = json.loads(Path(sys.argv[1]).read_text())
    shots_dir = Path(sys.argv[2])
    shots = {}
    for beat in contract.get("beats", []):
        f = shots_dir / f"{beat['id']}.png"
        if f.exists():
            shots[beat["id"]] = f.read_bytes()
    review = validate_contract(contract, shots)
    # Dump structured report alongside the screenshots so subsequent agents
    # (or a human reading later) can read it without re-running Opus.
    out_json = shots_dir / "validation.json"
    out_json.write_text(json.dumps(review.to_dict(), indent=2))
    print(review.to_text())
    print(f"\n[wrote] {out_json}")
    sys.exit(0 if review.is_ok() else 1)


if __name__ == "__main__":
    main()
