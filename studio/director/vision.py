"""vision.py — phone photo -> a brief the SPATAIL brain can explain.

The user points their phone at something they want explained (a diagram, an object,
a page) and we identify it with Gemini vision, returning {subject, summary, prompt}.
That brief feeds the same understanding -> director -> experience pipeline as pasted
text, so a picture and a paragraph land in the same place.

Key from ~/.spatail/secrets.env (never committed).
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

_SECRETS = Path.home() / ".spatail" / "secrets.env"
MODEL = "gemini-2.5-flash"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


def _key() -> str:
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    if _SECRETS.exists():
        for line in _SECRETS.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("GEMINI_API_KEY not set")


PROMPT = """You are SPATAIL's vision front-end. The user photographed something and asked:
"{question}". Identify the MAIN subject and explain HOW IT WORKS so we can show it as an
interactive, ANIMATED 3D/AR mechanism overlaid on the real object. Return STRICT JSON:
{"subject": "<short noun phrase, the thing to explain>",
 "summary": "<2-3 sentence plain-language answer to the user's question>",
 "answer": "<one concise sentence directly answering the question>",
 "prompt": "<an imperative request like 'Explain how a <subject> works'>",
 "mechanism_brief": "<instructions to a 3D artist to FIRST recreate the recognizable real object
   faithfully — it must clearly read as the actual object in the photo (correct overall shape,
   proportions, distinct named PARTS, and colors/materials) — and THEN animate it to answer the
   question. Describe the parts, how they connect, and the MOTION or transform that explains how it
   works as a smooth ~4-second seamless loop (parts moving, opening, highlighting, or exploding to
   reveal internals). Physically faithful, tabletop-scaled (<= 0.5 m). Model a real recognizable
   object with detail — NEVER abstract boxes or primitives.>",
 "confidence": <0..1>}
Pick the single most teachable subject. If text is visible and central, you may read it.
If the question is empty, treat it as "How does this work?". User note (may be empty): {note}"""


def brief_from_image(image_bytes: bytes, *, mime: str = "image/jpeg", note: str = "",
                     question: str = "") -> dict:
    q = (question or "").strip() or "How does this work?"
    prompt = PROMPT.replace("{question}", q).replace("{note}", note or "")
    parts = [{"text": prompt},
             {"inlineData": {"mimeType": mime, "data": base64.b64encode(image_bytes).decode()}}]
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3}}
    req = urllib.request.Request(API.format(model=MODEL, key=_key()),
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    r = urllib.request.urlopen(req, timeout=90)
    data = json.load(r)
    txt = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    obj = json.loads(txt)
    obj.setdefault("subject", "the subject")
    obj.setdefault("summary", "")
    obj.setdefault("prompt", f"Explain {obj['subject']}")
    obj.setdefault("answer", obj.get("summary", ""))
    obj.setdefault("mechanism_brief",
                   f"Build and animate a clear, physically faithful 3D model of {obj['subject']} "
                   f"that demonstrates how it works, as a smooth 4-second seamless loop, tabletop-scaled.")
    obj["question"] = q
    return obj


def brief_from_path(path: str, note: str = "", question: str = "") -> dict:
    p = Path(path)
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return brief_from_image(p.read_bytes(), mime=mime, note=note, question=question)
