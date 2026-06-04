"""llm.py — the Gemini-backed Director agent (gives the director real freedom).

Given the content + the assets + the mechanics catalog (filtered to what's usable for
these assets), it asks Gemini to DESIGN the experience: which mechanics, on which
targets, with what params/triggers, grouped into beats. Returns the raw beats list;
director.validate_beats then clamps it to the catalog. Strict-JSON output.

Key from ~/.spatail/secrets.env (never committed). If the key/model is unavailable,
compose() raises and the director falls back to its deterministic composer.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mechanics"))
import catalog as mech  # noqa: E402

_SECRETS = Path.home() / ".spatail" / "secrets.env"
MODEL = "gemini-2.5-flash"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


def _key() -> str:
    import os
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    if _SECRETS.exists():
        for line in _SECRETS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY=") and "=" in line:
                return line.split("=", 1)[1].strip()
    raise RuntimeError("GEMINI_API_KEY not set")


def _mechanics_brief(caps: set) -> str:
    lines = []
    for m in mech.MECHANICS:
        if not mech.usable(m.id, caps):
            continue
        ps = ", ".join(f"{n}:{s['type']}" for n, s in m.params.items()) or "—"
        lines.append(f"- {m.id} [{m.category}]: {m.summary} | params: {ps} | "
                     f"triggers: {'/'.join(m.triggers)}")
    return "\n".join(lines)


def _assets_brief(assets: list[dict]) -> str:
    out = []
    for a in assets:
        caps = []
        if a.get("supportsAnimation"): caps.append("animation")
        if a.get("supportsHighlight"): caps.append("named_parts")
        if a.get("supportsTransparency"): caps.append("transparency")
        out.append(f"- id={a['id']} | name={a.get('name', a['id'])} | role={a.get('role', 'object')}"
                   f" | caps={','.join(caps) or 'basic'}")
    return "\n".join(out)


PROMPT = """You are the SPATAIL Director — an expert at designing spatial AR explanations.
You teach a concept by composing MECHANICS (small animation / interaction / annotation
units) onto 3D assets in an ordered sequence of beats. You have full creative freedom in
WHICH mechanics you use, their order, parameters, and triggers — pick whatever explains
THIS content best. Make motion match real behaviour (a pendulum oscillates, planets spin
and orbit, an engine's piston reciprocates). Prefer variety and genuine interactivity
(let the user tap, grab, scrub, or answer where it helps). Keep 3–6 beats.

AVAILABLE MECHANICS (only use these ids):
{mechanics}

ASSETS (use these exact ids as targets, or "scene"):
{assets}

CONTENT TO EXPLAIN:
domain: {domain}
intent: {intent}
subject: {subject}
summary: {summary}

Return STRICT JSON ONLY, no prose, in this exact shape:
{{"title": "...",
  "beats": [
    {{"id": "short_id", "title": "...", "narration": "one or two sentences",
      "focus": "<assetId>",
      "mechanics": [
        {{"mechanic": "<id from the list>", "target": "<assetId or scene>",
          "params": {{}}, "trigger": "auto|on_tap|on_focus|on_step|on_grab|on_slider"}}
      ]}}
  ]}}
Design the most vivid, correct, interactive explanation you can."""


def compose(understanding: dict, assets: list[dict], caps: set) -> list[dict]:
    prompt = PROMPT.format(
        mechanics=_mechanics_brief(caps), assets=_assets_brief(assets),
        domain=understanding.get("domain", ""), intent=understanding.get("intent", ""),
        subject=understanding.get("subject", ""), summary=understanding.get("summary", ""))
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7}}
    req = urllib.request.Request(API.format(model=MODEL, key=_key()),
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    r = urllib.request.urlopen(req, timeout=90)
    data = json.load(r)
    txt = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    obj = json.loads(txt)
    return obj.get("beats", [])
