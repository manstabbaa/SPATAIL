"""llm.py — the Gemini-backed Director, as a CLIP SEQUENCER.

Motion is baked into each asset as named clips (asset_manifest.py). Given the assets,
their clips, and their parts, Gemini DESIGNS the lesson by sequencing those clips into
ordered steps (each plays a clip + narration + optional panels). Returns the raw
sequence; composer._validate_sequence clamps it. Strict-JSON output.

Key from ~/.spatail/secrets.env. If unavailable, compose() raises and the composer
falls back to its deterministic sequencer.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

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


def _assets_brief(assets: list[dict]) -> str:
    out = []
    for a in assets:
        clips = ", ".join(c.get("name", "") for c in (a.get("clips") or [])) or "demo"
        parts = ", ".join(p.get("name", "") for p in (a.get("parts") or [])[:12]) or "—"
        out.append(f"- id={a['id']} name={a.get('name', a['id'])} role={a.get('role', 'object')}"
                   f" | clips: {clips} | parts: {parts}")
    return "\n".join(out)


PROMPT = """You are the SPATAIL Director — you design spatial AR lessons. Motion is
already BAKED into each asset as named animation CLIPS. You DESIGN the lesson by
SEQUENCING those clips into ordered steps: each step plays one clip on a focus asset,
with spoken narration and optional text panels. You do NOT invent motion — only use
clip names listed for the asset. Keep 3-6 steps; build understanding in order.

ASSETS (use these exact ids for focus, and only these clip names):
{assets}

CONTENT:
domain: {domain}
intent: {intent}
subject: {subject}
summary: {summary}

Return STRICT JSON ONLY in this shape:
{{"sequence": [
  {{"id": "short_id", "title": "...", "narration": "1-2 sentences",
    "focus": "<assetId>", "clip": "<clip name on that asset>", "loop": true,
    "panels": [{{"kind": "title|fact|caption|quiz", "title": "...", "body": "...",
                "question": "...", "options": [".."], "answer": 0}}],
    "advance": "tap"}}
]}}
Design the clearest, most vivid sequence you can."""


def compose(understanding: dict, assets: list[dict], clips: list[dict]) -> list[dict]:
    prompt = PROMPT.format(
        assets=_assets_brief(assets),
        domain=understanding.get("domain", ""), intent=understanding.get("intent", ""),
        subject=understanding.get("subject", ""), summary=understanding.get("summary", ""))
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7}}
    req = urllib.request.Request(API.format(model=MODEL, key=_key()),
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    # Tight cap: /modular is SYNCHRONOUS for the phone and the web viewer — a slow
    # LLM must fail fast into the deterministic sequencer, not stall the contract.
    r = urllib.request.urlopen(req, timeout=30)
    data = json.load(r)
    txt = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    return json.loads(txt).get("sequence", [])
