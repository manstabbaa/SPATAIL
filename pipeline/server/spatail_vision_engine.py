"""
spatail_vision_engine.py — the PC "live intelligence engine" for SPATAIL.

Turns the PC into a real-time vision brain: the phone streams camera frames in,
an NVIDIA VLM identifies what is in view, and identifications stream back to the
phone AND to a browser debug view you can watch over Parsec ("see what it sees").

    iPhone camera ─(binary JPEG over WS)─► THIS ENGINE ─► VLM (OpenAI-compatible)
                                               │
                                               ├─► identifications back to phone (JSON over WS)
                                               └─► /  browser overlay  (frame + boxes + latency)

This is the TRACKED-stream front door — "what is the user looking at?" — and is
deliberately SEPARATE from the prompt→Blender PLACED-stream gateway in
spatail_session_server.py. The two converge later: a confident identification can
auto-fire the equivalent of a `user.prompt` into that pipeline so the explanation
builds for whatever the camera is pointed at. See docs/xr/REALTIME_PROTOCOL.md §10.

Why a VLM on the PC (vs Apple on-device object tracking):
    - Open vocabulary — identifies things you never trained a reference object for.
    - You own it, can swap models, and can watch its raw output over Parsec.
    - Round-trip latency (~0.5–2 s) is fine because the VLM only sets WHAT + roughly
      WHERE; the phone's ARKit handles continuous 6-DoF pose every frame.

VLM adapter:
    Defaults to an OpenAI-compatible /chat/completions vision endpoint, which
    NVIDIA NIM, Ollama, and vLLM all expose. Point it at your server with
    --vlm-url / --vlm-model. To go native (e.g. a NIM detection model that
    returns boxes), subclass VLMAdapter and pass it to VisionEngine.

Run — engine only, verify with static images, NO phone needed:
    python pipeline/server/spatail_vision_engine.py \\
        --vlm-url http://localhost:11434/v1 --vlm-model qwen2.5vl:7b \\
        --test-dir ./assets_raw
    # then open  http://<pc-ip>:8799/  in a browser (over Parsec)

Run — live, phone streams frames:
    python pipeline/server/spatail_vision_engine.py \\
        --vlm-url http://localhost:8000/v1 --vlm-model <model>
    # phone connects to  ws://<pc-ip>:8798/v1/vision  and POSTs binary JPEG frames

Deps (same as the session server, no new ones):
    pip install websockets>=12 aiohttp>=3.9
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import websockets
    try:
        # websockets ≥ 13 renamed the class; the old path emits a
        # DeprecationWarning at boot. The name is annotation-only here
        # (from __future__ import annotations), so either resolves fine.
        from websockets.asyncio.server import ServerConnection as WebSocketServerProtocol
    except ImportError:  # older websockets
        from websockets.server import WebSocketServerProtocol
    from aiohttp import web, ClientSession, ClientTimeout
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependencies. Install with:\n"
        "    pip install websockets aiohttp\n"
        f"Original error: {exc}"
    )

log = logging.getLogger("spatail.vision")

# The fusion brain (fuse VLM label + room scan → placement contract) lives in
# node — pipeline/spatail/plan_from_room.js. The engine shells out to it so
# there is exactly ONE brain implementation shared by the CLI, the tests, and
# this live path.
BRAIN_SCRIPT = Path(__file__).resolve().parents[2] / "pipeline" / "spatail" / "plan_from_room.js"

# Every replan is persisted for offline replay (LIVE_BRAIN_SPEC §2.1):
# studio/out/traces/vision/<session-stamp>/plan_NNNN.json
VISION_TRACE_ROOT = Path(__file__).resolve().parents[2] / "studio" / "out" / "traces" / "vision"

# Replan gate knobs (LIVE_BRAIN_SPEC §1.4).
REPLAN_MIN_CONF_ENV = "SPATAIL_REPLAN_MIN_CONF"
REPLAN_DWELL_ENV = "SPATAIL_REPLAN_DWELL"
REPLAN_MIN_CONF_DEFAULT = 0.55
REPLAN_DWELL_DEFAULT = 2


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning(f"{name}={raw!r} is not a number; using {default}")
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning(f"{name}={raw!r} is not an integer; using {default}")
        return default


# ────────────────────────────────────────────────────────────────────────
# Identification result
# ────────────────────────────────────────────────────────────────────────

@dataclass
class Detection:
    """One identified thing in the current frame.

    box is normalized [x, y, w, h] in 0..1 with origin top-left, or None when
    the VLM only returns a label (most chat VLMs are weak at precise boxes — we
    treat the box as a hint, not ground truth).
    """
    label: str
    confidence: float = 0.0
    box: list[float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "confidence": round(self.confidence, 3), "box": self.box}


@dataclass
class IdentifyResult:
    detections: list[Detection]
    primary: str | None
    raw_text: str
    latency_ms: int
    model: str
    # Sub-parts of the primary object ({label, box?, confidence?}) — spec §1.3.
    parts: list[dict[str, Any]] = field(default_factory=list)
    # Dossier of the primary object (LIVE_BRAIN_SPEC §5.3), already wire-shaped
    # by _clean_attributes: {colors[], materials[], textContent[], language,
    # brand, state} — only fields the VLM was confident about survive.
    attributes: dict[str, Any] | None = None
    # Capture/ingest time (epoch seconds) of the frame this identity describes.
    frame_timestamp: float | None = None
    # (width, height) of the identified frame — the pixel space the VLM's raw
    # boxes were normalized out of. None when the header couldn't be parsed.
    frame_size: tuple[int, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "detections": [d.to_dict() for d in self.detections],
            "primary": self.primary,
            "rawText": self.raw_text,
            "latencyMs": self.latency_ms,
            "model": self.model,
            "parts": self.parts,
            "attributes": self.attributes,
            "frameTimestamp": self.frame_timestamp,
            "frameSize": list(self.frame_size) if self.frame_size else None,
        }


# ────────────────────────────────────────────────────────────────────────
# VLM adapter — OpenAI-compatible vision chat (NIM / Ollama / vLLM)
# ────────────────────────────────────────────────────────────────────────

# Boxes are requested in PIXEL [x1, y1, x2, y2], not normalized [x, y, w, h]:
# grounding-capable VLMs (Qwen2.5-VL) are trained to emit absolute pixel
# corner coordinates and simply ignore a "respond normalized" instruction —
# observed live: 766/766 plans came back in pixels, and a warm qwen2.5vl:3b
# echoed xyxy even when asked for xywh. Ask for what the model actually does;
# the engine knows the frame size (_image_size) and converts server-side.
# _clean_box still accepts 0..1 xywh replies from models that do obey.
_IDENTIFY_PROMPT = (
    "You are the vision system of an AR teaching app. Look at the image and "
    "identify the single most prominent real-world OBJECT the user is pointing "
    "their camera at (ignore background, hands, walls, floor). "
    "Respond with ONLY a JSON object, no prose, of the form:\n"
    '{"primary": "<short noun, e.g. \\"car engine air filter\\">", '
    '"confidence": <0..1>, '
    '"alternatives": [{"label": "<noun>", "confidence": <0..1>}], '
    '"box": [x1, y1, x2, y2], '
    '"parts": [{"label": "<part noun, e.g. \\"cap\\">", "box": [x1, y1, x2, y2]}], '
    '"attributes": {"colors": ["<color>"], "materials": ["<material>"], '
    '"text_content": ["<visible text>"], "language": "<language of that text>", '
    '"brand": "<brand>", "state": "<open/closed/on/off/…>"} }\n'
    "box is the bounding box of the primary object in image pixels — "
    "[left, top, right, bottom] corner coordinates, origin top-left; use null "
    "if unsure. parts lists the primary object's visible sub-parts (cap, lid, "
    "handle, button, …) with boxes in the same pixel space; use [] if unsure. "
    "attributes describes the PRIMARY object only (PERCEPTION_V3 dossier) — "
    "include ONLY the fields you are confident about from what is visible and "
    "omit the rest. Keep labels concrete and specific."
)


# Focus-pass prompt (PERCEPTION_V3 §7, LIVE_BRAIN_SPEC §5.2) — the crop is ONE
# already-tracked object at close range, so identity comes first (the registry
# label may be shallow), then the wanted dossier fields, then the user's
# question answered from what is actually visible. Boxes stay pixel xyxy like
# the ambient prompt — the engine normalizes against the CROP via _clean_box.
_FOCUS_ATTR_KEYS = ("colors", "materials", "text_content", "language", "brand", "state")
_FOCUS_LIST_KEYS = frozenset(("colors", "materials", "text_content"))


def _focus_prompt(question=None, wanted=None) -> str:
    """Pure prompt builder for a vision.focus pass. `wanted` arrives in wire
    camelCase (LIVE_BRAIN_SPEC §5.2); unknown names are dropped (their reply
    keys would not survive _clean_attributes anyway); empty/absent wanted
    requests the full dossier shape."""
    keys: list[str] = []
    for name in (wanted if isinstance(wanted, (list, tuple)) else []):
        if not isinstance(name, str):
            continue
        key = name.strip()
        key = "text_content" if key in ("textContent", "text_content") else key
        if key in _FOCUS_ATTR_KEYS and key not in keys:
            keys.append(key)
    if not keys:
        keys = list(_FOCUS_ATTR_KEYS)
    attr_shape = ", ".join(
        f'"{k}": [".."]' if k in _FOCUS_LIST_KEYS else f'"{k}": ".."' for k in keys)
    fields = [
        '"primary": "<short specific noun, brand/model if readable>"',
        '"confidence": <0..1>',
        '"attributes": {%s}' % attr_shape,
    ]
    q = None
    if isinstance(question, str) and question.strip():
        # one line, no double quotes — the question is embedded in the prompt
        q = " ".join(question.split()).replace('"', "'")[:200]
    if q:
        fields.append('"answer": "<one sentence>"')
    fields.append('"parts": [{"label": "<part noun>", "box": [x1, y1, x2, y2]}]')
    prompt = (
        "This is a close-up crop of ONE real-world object. Identify the object "
        "in this close-up crop — a short, specific noun. "
        "Respond with ONLY a JSON object, no prose, of the form:\n"
        "{" + ", ".join(fields) + " }\n"
        "attributes: include ONLY the listed fields you are confident about "
        "from what is visible; omit the rest. parts lists the object's visible "
        "sub-parts with boxes in image pixels — [left, top, right, bottom] "
        "corner coordinates, origin top-left; use [] if unsure."
    )
    if q:
        prompt += (
            f" answer holds a one-sentence answer to the question “{q}”, "
            "grounded ONLY in what is visible in this image; if the image "
            "cannot answer it, say so briefly."
        )
    return prompt


class VLMAdapter:
    """Calls an OpenAI-compatible /chat/completions endpoint with an image.

    Works against NVIDIA NIM, Ollama, and vLLM out of the box. Override
    `identify` for a native API that returns real detection boxes.
    """

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 prompt: str = _IDENTIFY_PROMPT, timeout_s: float = 8.0,
                 max_tokens: int = 450):
        # 450 tokens (was 300): the attributes dossier rides the same reply
        # (PERCEPTION_V3 §8) — 300 was sized for primary+parts alone and would
        # truncate mid-JSON with the dossier added.
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.prompt = prompt
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens

    async def identify(self, jpeg: bytes, http: ClientSession,
                       prompt: str | None = None) -> IdentifyResult:
        # `prompt` overrides the ambient identify prompt per-call — focus
        # passes (LIVE_BRAIN_SPEC §5.2) send a question-conditioned one; the
        # reply shape stays parseable by the same machinery.
        # The pixel space the VLM will echo boxes in — needed to normalize them.
        frame_size = _image_size(jpeg)
        data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0.0,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt or self.prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        t0 = time.monotonic()
        text = ""
        try:
            async with http.post(f"{self.base_url}/chat/completions", json=body,
                                 headers=headers,
                                 timeout=ClientTimeout(total=self.timeout_s)) as resp:
                if resp.status != 200:
                    detail = (await resp.text())[:200]
                    raise RuntimeError(f"VLM HTTP {resp.status}: {detail}")
                data = await resp.json()
                text = (data["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:
            latency = int((time.monotonic() - t0) * 1000)
            # asyncio.TimeoutError stringifies to "" — name the failure honestly
            # (observed at cold start while Ollama loads the model).
            reason = str(exc).strip() or exc.__class__.__name__
            if isinstance(exc, asyncio.TimeoutError):
                reason = f"timeout after {latency}ms (--vlm-timeout {self.timeout_s:g}s)"
            log.warning(f"VLM call failed in {latency}ms: {reason}")
            return IdentifyResult([], None, f"<error: {reason}>", latency, self.model,
                                  frame_size=frame_size)

        latency = int((time.monotonic() - t0) * 1000)
        dets, primary, parts, attrs = _parse_identify_json(text, frame_size)
        return IdentifyResult(dets, primary, text, latency, self.model, parts=parts,
                              attributes=attrs, frame_size=frame_size)


def _parse_identify_json(text: str, frame_size: tuple[int, int] | None = None,
                         ) -> tuple[list[Detection], str | None,
                                    list[dict[str, Any]], dict[str, Any] | None]:
    """Best-effort extraction of (detections, primary, parts, attributes) from
    a chat VLM reply.

    VLMs wrap JSON in code fences or prose; pull the first {...} blob. If it
    can't be parsed at all, fall back to treating the whole reply as one label.
    frame_size is the (width, height) of the frame the VLM saw — pixel-space
    boxes are normalized against it (see _clean_box).
    """
    blob = None
    fence = re.search(r"\{.*\}", text, re.DOTALL)
    if fence:
        try:
            blob = json.loads(fence.group(0))
        except json.JSONDecodeError:
            blob = None
    if blob is None or not isinstance(blob, dict):
        label = text.strip().strip(".")[:80]
        return (([Detection(label=label, confidence=0.3)] if label else []),
                (label or None), [], None)

    dets: list[Detection] = []
    primary = blob.get("primary") or blob.get("label")
    if primary:
        dets.append(Detection(
            label=str(primary),
            confidence=float(blob.get("confidence", 0.0) or 0.0),
            box=_clean_box(blob.get("box"), frame_size),
        ))
    for alt in (blob.get("alternatives") or []):
        if isinstance(alt, dict) and alt.get("label"):
            dets.append(Detection(label=str(alt["label"]),
                                  confidence=float(alt.get("confidence", 0.0) or 0.0),
                                  box=_clean_box(alt.get("box"), frame_size)))
    return (dets, (str(primary) if primary else None),
            _clean_parts(blob.get("parts"), frame_size),
            _clean_attributes(blob.get("attributes")))


def _clean_parts(raw: Any, frame_size: tuple[int, int] | None = None) -> list[dict[str, Any]]:
    """Sub-parts of the primary object (spec §1.3). The VLM is free-form here —
    anything that isn't {label[, box, confidence]} is dropped so a creative
    reply can never break identification."""
    if not isinstance(raw, list):
        return []
    parts: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            continue
        part: dict[str, Any] = {"label": label.strip()[:80],
                                "box": _clean_box(item.get("box"), frame_size)}
        conf = item.get("confidence")
        if isinstance(conf, (int, float)):
            part["confidence"] = round(max(0.0, min(1.0, float(conf))), 3)
        parts.append(part)
        if len(parts) >= 16:
            break
    return parts


def _clean_attributes(raw: Any) -> dict[str, Any] | None:
    """Sanitize the VLM's attributes blob into the wire dossier shape
    (LIVE_BRAIN_SPEC §5.1: colors/materials/textContent as bounded string
    lists, language/brand/state as short strings — textContent is camelCase
    ON THE WIRE, the prompt asks snake_case, both are accepted in). Free-form
    input: a bare string where a list belongs becomes a 1-list, junk items are
    dropped, strings clip at 48 chars, lists at 6 entries, and an empty
    dossier collapses to None so the wire never carries {}."""
    if not isinstance(raw, dict):
        return None

    def strings(value: Any) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return []
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip()[:48])
            if len(out) >= 6:
                break
        return out

    def string(value: Any) -> str | None:
        if isinstance(value, (list, tuple)):    # ["English"] → "English"
            value = next((v for v in value if isinstance(v, str) and v.strip()), None)
        if isinstance(value, str) and value.strip():
            return value.strip()[:48]
        return None

    clean: dict[str, Any] = {}
    for key, wire in (("colors", "colors"), ("materials", "materials"),
                      ("text_content", "textContent")):
        vals = strings(raw.get(key))
        if not vals and wire != key:            # accept the camelCase alias in
            vals = strings(raw.get(wire))
        if vals:
            clean[wire] = vals
    for key in ("language", "brand", "state"):
        val = string(raw.get(key))
        if val is not None:
            clean[key] = val
    return clean or None


def _extract_answer(text: str) -> str | None:
    """The focus reply's one-sentence answer (LIVE_BRAIN_SPEC §5.2), pulled
    from the same first-{...} blob _parse_identify_json reads. Anything that
    isn't a usable short string (a bare number — a counting question — is
    stringified) is dropped; never raises."""
    fence = re.search(r"\{.*\}", text, re.DOTALL)
    if not fence:
        return None
    try:
        blob = json.loads(fence.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(blob, dict):
        return None
    answer = blob.get("answer")
    if isinstance(answer, (int, float)) and not isinstance(answer, bool):
        answer = str(answer)
    if isinstance(answer, str) and answer.strip():
        return answer.strip()[:300]
    return None


def _clean_box(box: Any, frame_size: tuple[int, int] | None = None) -> list[float] | None:
    """Coerce a VLM box into the wire contract: normalized [x, y, w, h] in
    0..1, origin top-left (WireMessages.swift / the debug overlay both assume
    this space).

    Grounding VLMs echo boxes in the pixel space of the frame they saw, as
    [x1, y1, x2, y2] corners — that is what they are trained on and what the
    prompt now asks for — so pixel boxes are converted + normalized against
    frame_size (with an [x, y, w, h] fallback when the corner reading is
    geometrically impossible). Boxes already in 0..1 are treated as the wire's
    xywh and pass through. A pixel box with no known frame size is dropped
    rather than misleading the overlay/brain; a degenerate (zero-area) box is
    dropped rather than clamped into existence.
    """
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        vals = [float(v) for v in box]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in vals):
        return None
    x, y, w, h = vals
    if all(v <= 1.5 for v in vals):
        # already normalized 0..1 — read as the wire's [x, y, w, h], unless it
        # provably was [x1, y1, x2, y2] (the xywh reading escapes the unit
        # square while the corner reading is a valid box)
        if (x + w > 1.05 or y + h > 1.05) and w > x and h > y:
            w, h = w - x, h - y
    elif frame_size:
        fw, fh = frame_size
        # pixel space: corners first (the prompt's format), xywh as fallback
        x1, y1, x2, y2 = vals
        if x2 > x1 and y2 > y1:
            x, y, w, h = x1, y1, x2 - x1, y2 - y1
        # else keep the xywh reading — a model that echoed width/height
        x, y, w, h = x / fw, y / fh, w / fw, h / fh
    else:
        return None
    # clamp into the unit square (VLMs run a few px past the edge)
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    w = min(max(w, 0.0), 1.0 - x)
    h = min(max(h, 0.0), 1.0 - y)
    if w <= 0.0 or h <= 0.0:
        return None
    return [round(v, 4) for v in (x, y, w, h)]


def _image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a JPEG/PNG header — no image libs (the engine's
    deps are deliberately just websockets+aiohttp; the browser does all the
    real decoding). JPEG: scan markers for a SOFn frame header. PNG: IHDR.
    Returns None on anything unrecognizable — callers treat that as "pixel
    boxes can't be normalized", never as an error."""
    # PNG — the test feeder sends .png files as-is
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24 and data[12:16] == b"IHDR":
        w = int.from_bytes(data[16:20], "big")
        h = int.from_bytes(data[20:24], "big")
        return (w, h) if w > 0 and h > 0 else None
    # JPEG — SOF0/1/2/… carry [precision u8][height u16][width u16]
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(data)
    while i + 9 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:                      # fill byte
            i += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:   # standalone
            i += 2
            continue
        if marker == 0xDA:                      # start of scan — SOF was missed
            return None
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        if seg_len < 2:
            return None
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return (w, h) if w > 0 and h > 0 else None
        i += 2 + seg_len
    return None


# Mirrors labelToSurfaceKind() in pipeline/spatail/surface_fusion.js — the
# open-vocabulary VLM noun mapped onto the scan's closed set of surface kinds.
# Keep the two in lockstep: the replan gate (spec §1.4) must agree with the
# fusion brain about what counts as "the same binding".
_SURFACE_KIND_PATTERNS = (
    ("table", re.compile(r"\b(table|desk|counter|workbench|nightstand|dresser|bench)\b")),
    ("floor", re.compile(r"\b(floor|ground|rug|carpet)\b")),
    ("wall", re.compile(r"\b(wall|door|window|whiteboard)\b")),
    ("ceiling", re.compile(r"\b(ceiling)\b")),
    ("seat", re.compile(r"\b(chair|seat|stool|sofa|couch)\b")),
)


def _label_to_surface_kind(label: Any) -> str | None:
    if not label:
        return None
    s = str(label).lower()
    for kind, pattern in _SURFACE_KIND_PATTERNS:
        if pattern.search(s):
            return kind
    return None


# Mirrors NOISE_ADJECTIVES / normalizeNoun() in surface_fusion.js — colors and
# materials the VLM prepends ("blue water bottle") that device object labels
# usually omit, stripped before containment matching. Same lockstep rule as
# the surface-kind patterns above.
_NOISE_ADJECTIVES = frozenset((
    "red", "orange", "yellow", "green", "blue", "purple", "violet", "pink",
    "black", "white", "gray", "grey", "brown", "beige", "tan", "silver",
    "gold", "golden", "dark", "light", "pale", "bright", "clear",
    "transparent", "shiny", "matte",
    "plastic", "metal", "metallic", "wooden", "wood", "glass", "ceramic",
    "leather", "steel", "aluminum", "aluminium", "rubber", "foam", "paper",
    "cardboard", "fabric", "cloth", "stone", "marble", "chrome", "brass",
    "copper", "stainless",
))


def _normalize_noun(text: Any) -> str:
    if not text:
        return ""
    words = re.sub(r"[^a-z0-9\s]", " ", str(text).lower()).split()
    kept = [w for w in words if w not in _NOISE_ADJECTIVES]
    # an all-adjective noun ("glass") still deserves a match attempt
    return " ".join(kept or words)


# ────────────────────────────────────────────────────────────────────────
# Vision engine — frame ingest + single-flight VLM worker + observability
# ────────────────────────────────────────────────────────────────────────

class VisionEngine:
    def __init__(self, vlm: VLMAdapter, min_interval_s: float = 0.4):
        self.vlm = vlm
        self.min_interval_s = min_interval_s  # don't hammer the VLM faster than this

        self._latest_frame: bytes | None = None
        self._latest_frame_ts: float | None = None  # epoch seconds at ingest
        self._frame_ready = asyncio.Event()
        self._result: IdentifyResult | None = None
        self._last_result_at: float | None = None
        self._clients: set[WebSocketServerProtocol] = set()

        # rolling stats for the debug view
        self._frames_in = 0
        self._infers = 0
        self._last_frame_at = 0.0
        self._ingest_fps = 0.0

        # fusion state — the room scan the phone streamed up, the camera pose,
        # and the brain's latest placement plan (see _replan()). The room is
        # scoped to the connection that sent it (_room_owner): plans derived
        # from it go to that socket alone and die with it, so a stale scan
        # can never be replayed into a different phone's coordinate system.
        self._room: dict | None = None
        self._room_owner: WebSocketServerProtocol | None = None
        self._pose: dict | None = None
        self._objects: list = []            # room.update objects[] (spec §1.2)
        self._concept: str | None = None    # room.update concept — lets the
        # phone's Ask flow drive part-addressed live plans (plan_from_room.js
        # only emits a {objectId, part} target when the concept names the part)
        self._debug_identification: dict | None = None
        self._last_plan: dict | None = None
        self._plan_version = 0
        self._plan_lock = asyncio.Lock()
        self._replan_tasks: set = set()     # detached replans (spec §1.4)
        self._inflight_bindings: set = set()  # bindings currently being planned
        # room.update replans are coalesced latest-wins (spec §0.3: no
        # unbounded queues) — while one is queued/running, further
        # room.updates only mark it dirty and the runner replans ONCE more
        # against the newest room. Identification replans are already
        # coalesced by _inflight_bindings.
        self._room_replan_active = False
        self._room_replan_dirty = False

        # focus passes (PERCEPTION_V3 §7, LIVE_BRAIN_SPEC §5.2): question-driven
        # hi-res crop identifies run as DETACHED tasks so a slow focus can never
        # stall the ambient vision.identification loop; the lock keeps focus VLM
        # calls single-flight (the GPU already serves the ambient worker), and
        # _focus_pending coalesces latest-wins per objectId — a newer ask about
        # the same thing replaces a queued-but-not-started one.
        self._focus_lock = asyncio.Lock()
        self._focus_pending: dict[str, dict] = {}   # objectId → newest queued request
        self._focus_tasks: set = set()

        # replan gate (spec §1.4): the plan is bound to an identity — a mapped
        # surface kind or a tracked object id — not to the raw label string.
        # ("surface"|"object", value) tuples; None = nothing bound yet.
        self.replan_min_conf = _env_float(REPLAN_MIN_CONF_ENV, REPLAN_MIN_CONF_DEFAULT)
        self.replan_dwell = max(1, _env_int(REPLAN_DWELL_ENV, REPLAN_DWELL_DEFAULT))
        self._last_planned_binding: tuple | None = None
        self._pending_binding: tuple | None = None
        self._pending_count = 0
        self._last_conf = 0.0
        self._last_replan_trigger: str | None = None

        # plan trace directory (spec §2.1 {session}) — rotated whenever room
        # ownership changes (see _handle_control) so plans from different
        # ARKit coordinate systems never interleave in one folder.
        self._trace_dir = self._mint_trace_dir()

    # -- frame ingest (called by the WS handler and the test feeder) -------

    def submit_frame(self, jpeg: bytes):
        now = time.monotonic()
        if self._last_frame_at:
            dt = now - self._last_frame_at
            if dt > 0:
                # exponential moving average of ingest rate
                self._ingest_fps = 0.8 * self._ingest_fps + 0.2 * (1.0 / dt)
        self._last_frame_at = now
        self._frames_in += 1
        self._latest_frame = jpeg
        self._latest_frame_ts = time.time()
        self._frame_ready.set()

    # -- single-flight inference loop -------------------------------------

    async def run_worker(self):
        """Always identify the FRESHEST frame; drop stale ones. One VLM call at
        a time so a slow model can't queue up a backlog."""
        async with ClientSession() as http:
            while True:
                await self._frame_ready.wait()
                self._frame_ready.clear()
                frame = self._latest_frame
                frame_ts = self._latest_frame_ts
                if frame is None:
                    continue
                result = await self.vlm.identify(frame, http)
                result.frame_timestamp = frame_ts
                self._infers += 1
                self._result = result
                self._last_result_at = time.time()
                await self._broadcast(result)
                log.info(
                    f"id#{self._infers} {result.latency_ms}ms "
                    f"primary={result.primary!r} "
                    f"({len(result.detections)} det, ingest {self._ingest_fps:.1f}fps)"
                )
                # Fusion replan gate (spec §1.4): raw label flapping never
                # replans — the BINDING (mapped surface kind or bound object
                # id) must change, the same binding must hold for
                # `replan_dwell` consecutive identifications, and the primary
                # confidence must clear `replan_min_conf`. Gated on the room's
                # owner still being connected — a room with no live owner must
                # never drive a replan.
                if result.primary:
                    binding = self._binding_for(result.primary)
                    if binding == self._pending_binding:
                        self._pending_count += 1
                    else:
                        self._pending_binding = binding
                        self._pending_count = 1
                    self._last_conf = (result.detections[0].confidence
                                       if result.detections else 0.0)
                    if (self._room is not None
                            and self._room_owner in self._clients
                            and binding != self._last_planned_binding
                            and binding not in self._inflight_bindings
                            and self._last_conf >= self.replan_min_conf
                            and self._pending_count >= self.replan_dwell):
                        self._spawn_replan(trigger=f"identification:{result.primary}")
                # throttle so we don't melt the GPU on a fast frame stream
                await asyncio.sleep(self.min_interval_s)

    async def _broadcast(self, result: IdentifyResult):
        await self._broadcast_json({
            "type": "vision.identification",
            "sentAt": datetime.now(timezone.utc).isoformat(),
            "payload": result.to_dict(),
        })

    async def _broadcast_json(self, obj: dict):
        if not self._clients:
            return
        msg = json.dumps(obj)
        dead = []
        for ws in self._clients:
            try:
                await ws.send(msg)
            except websockets.ConnectionClosed:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    # -- fusion: room ingest + brain replan ---------------------------------

    def _clear_room_state(self, reason: str):
        """Forget the room and everything derived from it. The room belongs
        to one connection; once that session is over, replanning against its
        geometry would hand the next client placements in a coordinate system
        that no longer exists (the live "stale sample room" incident)."""
        if self._room is None and self._room_owner is None and self._last_plan is None:
            return
        self._room = None
        self._room_owner = None
        self._pose = None
        self._objects = []
        self._concept = None
        self._debug_identification = None
        self._last_plan = None
        self._last_planned_binding = None
        self._plan_version = 0
        self._last_replan_trigger = None
        log.info(f"room state cleared ({reason})")

    def _binding_for(self, label: Any) -> tuple | None:
        """The identity a plan is bound to (spec §1.4) — objects-first: a
        tracked object whose label matches the VLM noun wins over the mapped
        surface kind, so re-wordings of the same thing never replan. Match
        semantics mirror matchNounToObject() in surface_fusion.js (containment
        after adjective stripping; exact match outranks containment, then the
        device's label confidence breaks ties) so the gate binds the same
        object id the brain will."""
        if not label:
            return None
        noun = _normalize_noun(label)
        best: tuple | None = None  # (score, object id)
        if noun:
            for obj in self._objects:
                if not isinstance(obj, dict) or not obj.get("id"):
                    continue
                have = _normalize_noun(obj.get("label"))
                if not have or (noun != have and noun not in have and have not in noun):
                    continue
                conf = obj.get("confidence")
                score = ((2.0 if noun == have else 1.0)
                         + (float(conf) if isinstance(conf, (int, float)) else 0.0))
                if best is None or score > best[0]:
                    best = (score, str(obj["id"]))
        if best is not None:
            return ("object", best[1])
        return ("surface", _label_to_surface_kind(label))

    def _spawn_replan(self, trigger: str):
        """Replans run detached (spec §1.4) so neither the frame receive loop
        nor the identification downlink ever waits on the brain; the existing
        _plan_lock still serializes the node subprocess."""
        task = asyncio.create_task(self._replan(trigger=trigger))
        self._replan_tasks.add(task)
        task.add_done_callback(self._replan_tasks.discard)

    @staticmethod
    def _mint_trace_dir() -> Path:
        """A fresh stamped session folder under the vision trace root — the
        `{session}` in spec §2.1's studio/out/traces/vision/{session}/. Suffixed
        on collision so a same-second owner handover can never overwrite an
        earlier session's plans."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = VISION_TRACE_ROOT / stamp
        n = 1
        while path.exists():
            n += 1
            path = VISION_TRACE_ROOT / f"{stamp}-{n}"
        return path

    def _persist_plan_trace(self, trigger: str, brain_input: dict, plan: dict):
        """Spec §2.1: every replan is archived for offline replay — brainInput
        is exactly what plan_from_room.js --stdin accepts. Best-effort: a full
        disk or bad path must never take down the live loop."""
        try:
            self._trace_dir.mkdir(parents=True, exist_ok=True)
            path = self._trace_dir / f"plan_{self._plan_version:04d}.json"
            path.write_text(json.dumps({
                "planVersion": self._plan_version,
                "trigger": trigger,
                "brainInput": brain_input,
                "plan": plan,
            }, indent=2))
        except Exception as exc:
            log.warning(f"plan trace persist failed (plan#{self._plan_version}): {exc}")

    async def _handle_control(self, ws: WebSocketServerProtocol, text: str):
        """Text messages on the frame WS are the phone's control channel.
        `room.update` carries the device's RoomContract (or a brain-shaped
        room) plus the camera pose and tracked objects; receiving one re-runs
        the brain. The sending socket becomes the room's owner — every plan
        derived from this room is delivered to it alone. `pose.update`
        (spec §1.1) keeps the gaze ray live between room sends and NEVER
        triggers a replan. `vision.focus` (LIVE_BRAIN_SPEC §5.2) carries a
        hi-res crop of one object and answers the sender directly."""
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            log.debug(f"frame-ws text (not JSON): {text[:120]}")
            return
        mtype = msg.get("type")
        if mtype == "pose.update":
            # Pose only steers plans for the held room, so only its owner may
            # move it — a debug client must not bend another phone's gaze ray.
            if self._room_owner is not None and ws is not self._room_owner:
                log.debug("pose.update ignored: not from the room owner")
                return
            payload = msg.get("payload") or {}
            if isinstance(payload, dict) and payload.get("position") and payload.get("forward"):
                self._pose = payload
            return
        if mtype == "vision.focus":
            await self._handle_focus(ws, msg.get("payload") or {})
            return
        if mtype != "room.update":
            log.debug(f"frame-ws control ignored: {mtype!r}")
            return
        payload = msg.get("payload") or {}
        # Accept {room, pose} or a bare RoomContract as the payload.
        self._room = payload.get("room") or payload
        if ws is not self._room_owner:
            # New owner = new client session/coordinate system → new trace
            # folder (spec §2.1 {session}); plan_NNNN restarts and stays
            # monotonic within the folder.
            self._trace_dir = self._mint_trace_dir()
            self._plan_version = 0
        self._room_owner = ws
        # Keep a live pose.update-fed pose when this room.update carries none.
        self._pose = payload.get("pose") or payload.get("userPose") or self._pose
        # objects[] (spec §1.2) may ride at payload level or inside the room;
        # attach them to the held room so the brain's room adapter sees them.
        objs = payload.get("objects") or (self._room or {}).get("objects") or []
        self._objects = objs if isinstance(objs, list) else []
        if isinstance(self._room, dict) and self._objects:
            self._room["objects"] = self._objects
        # Optional ask-context: when the phone's Ask flow scopes the live
        # session to a question ("explain the cap"), the brain can emit a
        # part-addressed target for it. Absent → cleared (stale questions
        # must not steer later plans).
        concept = payload.get("concept")
        self._concept = concept if isinstance(concept, str) and concept.strip() else None
        # Dev hook: lets a test client (or the HUD) pin the identification
        # without running a VLM — the brain path stays identical.
        self._debug_identification = payload.get("debugIdentification")
        n_surfaces = len((self._room or {}).get("surfaces") or [])
        log.info(f"room.update: {n_surfaces} surfaces, {len(self._objects)} objects, "
                 f"pose={'yes' if self._pose else 'no'}")
        if self._room_replan_active:
            # A room.update replan is already queued/running — coalesce
            # latest-wins (spec §0.3): its runner replans ONCE more against
            # the newest room instead of stacking a task per message.
            self._room_replan_dirty = True
        else:
            self._room_replan_active = True
            self._spawn_replan(trigger="room.update")

    # -- focus passes (PERCEPTION_V3 §7 / LIVE_BRAIN_SPEC §5.2) --------------

    # A "hi-res crop" bigger than this is not a crop — reject before it ever
    # reaches base64→VLM (the frame WS caps text messages well below this
    # anyway; the bound also protects direct/test callers).
    _FOCUS_MAX_JPEG = 8 * 1024 * 1024

    async def _handle_focus(self, ws: WebSocketServerProtocol, payload: Any):
        """Validate + enqueue one vision.focus request. Decode failures answer
        IMMEDIATELY with an error-shaped result to the requester — the phone's
        ask flow waits ≤ 3.5 s and must learn fast that nothing is coming."""
        if not isinstance(payload, dict):
            payload = {}
        request_id = payload.get("requestId")
        object_id = payload.get("objectId")
        raw = payload.get("jpegBase64")
        crop: bytes = b""
        error: str | None = None
        if not isinstance(raw, str) or not raw:
            error = "missing jpegBase64"
        else:
            try:
                # tolerate wrapped/whitespaced base64, reject genuine junk
                crop = base64.b64decode(re.sub(r"\s+", "", raw), validate=True)
            except Exception as exc:
                error = f"undecodable jpegBase64 ({exc})"
        if error is None and not crop:
            error = "empty jpeg"
        if error is None and len(crop) > self._FOCUS_MAX_JPEG:
            error = f"crop too large ({len(crop)} bytes > {self._FOCUS_MAX_JPEG})"
        if error is not None:
            log.warning(f"vision.focus {request_id!r} rejected: {error}")
            await self._send_focus_result(ws, {
                "requestId": request_id, "objectId": object_id,
                "label": None, "confidence": 0.0, "attributes": None,
                "answer": None, "parts": [], "latencyMs": 0,
                "model": self.vlm.model, "rawText": f"<error: {error}>",
            })
            return
        key = str(object_id) if object_id is not None else ""
        if key in self._focus_pending:
            # latest-wins per object (§7): the phone re-asked before the queued
            # pass started — the stale crop/question is dropped silently (the
            # superseded requestId never answers; the phone's 3.5 s wait
            # already covers that).
            log.debug(f"vision.focus for object {key or '?'} superseded before start")
        self._focus_pending[key] = {
            "ws": ws, "requestId": request_id, "objectId": object_id,
            "question": payload.get("question"), "wanted": payload.get("wanted"),
            "crop": crop,
        }
        # Detached like replans: the frame receive loop and the ambient worker
        # never wait on a focus VLM call.
        task = asyncio.create_task(self._run_focus(key))
        self._focus_tasks.add(task)
        task.add_done_callback(self._focus_tasks.discard)

    async def _run_focus(self, key: str):
        """Run one queued focus pass: single-flight on the focus lock, re-check
        the queue under it (latest-wins may have replaced or consumed the
        record), call the VLM on the crop with the question-conditioned
        prompt, and answer the REQUESTING socket only — never broadcast; a
        crop's pixel space means nothing to anyone but the asker."""
        reply: dict | None = None
        ws = None
        async with self._focus_lock:
            req = self._focus_pending.pop(key, None)
            if req is None:
                return      # superseded — an earlier task already ran the newest ask
            ws = req["ws"]
            question = req.get("question")
            prompt = _focus_prompt(question, req.get("wanted"))
            try:
                # Per-call session, created INSIDE the running loop (aiohttp
                # binds the loop at construction); the ambient worker's session
                # belongs to run_worker and is never shared across tasks.
                async with ClientSession() as http:
                    result = await self.vlm.identify(req["crop"], http, prompt=prompt)
            except Exception as exc:
                # vlm.identify catches its own HTTP/timeouts; this guards
                # adapter subclasses/session setup — a focus failure must
                # never kill the control channel or wedge the lock.
                log.warning(f"focus pass for object {key or '?'} failed: {exc}")
                reply = {
                    "requestId": req.get("requestId"), "objectId": req.get("objectId"),
                    "label": None, "confidence": 0.0, "attributes": None,
                    "answer": None, "parts": [], "latencyMs": 0,
                    "model": self.vlm.model, "rawText": f"<error: {exc}>",
                }
            else:
                conf = result.detections[0].confidence if result.detections else 0.0
                reply = {
                    "requestId": req.get("requestId"),
                    "objectId": req.get("objectId"),
                    "label": result.primary,
                    "confidence": round(conf, 3),
                    "attributes": result.attributes,
                    "answer": _extract_answer(result.raw_text),
                    # parts boxes normalized against the CROP (identify used
                    # _image_size(crop)); the phone converts back through the
                    # crop rect it sent (LIVE_BRAIN_SPEC §5.2).
                    "parts": result.parts,
                    "latencyMs": result.latency_ms,
                    "model": result.model,
                    "rawText": result.raw_text,
                }
                log.info(f"focus#{req.get('requestId')!r} {result.latency_ms}ms "
                         f"object={key or '?'} label={result.primary!r}")
        # send OUTSIDE the lock — a dead/backpressured requester must not
        # block the next focus pass.
        await self._send_focus_result(ws, reply)

    async def _send_focus_result(self, ws: WebSocketServerProtocol, payload: dict):
        """Requester-only downlink for a focus result (never broadcast)."""
        msg = json.dumps({
            "type": "vision.focus.result",
            "sentAt": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        })
        try:
            await ws.send(msg)
        except websockets.ConnectionClosed:
            self._clients.discard(ws)
            log.info(f"focus result {payload.get('requestId')!r} dropped: "
                     "requester disconnected")

    async def _replan(self, trigger: str):
        """Run the node fusion brain against (room, pose, identification) and
        send the resulting contract as an experience.delta to the room's
        owner ONLY — never broadcast: the coordinates only mean something to
        the client whose scan they came from."""
        owner = self._room_owner
        if self._room is None or owner is None:
            if trigger == "room.update":
                self._room_replan_active = False
                self._room_replan_dirty = False
            return
        ident = self._debug_identification
        if ident is None and self._result and self._result.primary \
                and not self._result.raw_text.startswith("<error"):
            # Same identification shape the phone gets (spec §1.3): parts ride
            # along so the brain can emit a part-addressed target, and
            # frameTimestamp keeps the persisted brainInput replayable as-is.
            ident = {
                "primary": self._result.primary,
                "detections": [d.to_dict() for d in self._result.detections],
                "parts": self._result.parts,
                "frameTimestamp": self._result.frame_timestamp,
            }
        # The binding this plan will be bound to — captured now, against the
        # objects the plan is actually computed from (spec §1.4). It is marked
        # in-flight until this task finishes so the worker gate can't queue a
        # duplicate replan of the same binding while the brain is thinking.
        planned_binding = self._binding_for((ident or {}).get("primary"))
        # room.objects is the ONE objects channel (plan_from_room.js reads
        # {room, pose?, identification?, concept?}); _handle_control already
        # attached objects[] to the held room.
        brain_input_obj = {
            "room": self._room, "pose": self._pose, "identification": ident,
        }
        if self._concept:
            brain_input_obj["concept"] = self._concept
        brain_input = json.dumps(brain_input_obj).encode()

        self._inflight_bindings.add(planned_binding)
        try:
            async with self._plan_lock:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "node", str(BRAIN_SCRIPT), "--stdin",
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    out, err = await asyncio.wait_for(
                        proc.communicate(brain_input), timeout=15)
                except FileNotFoundError:
                    log.warning("replan skipped: `node` not on PATH")
                    return
                except asyncio.TimeoutError:
                    proc.kill()
                    log.warning("replan: brain subprocess timed out")
                    return
                if proc.returncode != 0:
                    log.warning(f"replan failed rc={proc.returncode}: "
                                f"{err.decode(errors='replace')[:200]}")
                    return
                try:
                    plan = json.loads(out.decode())
                except json.JSONDecodeError as exc:
                    log.warning(f"replan: brain emitted bad JSON: {exc}")
                    return

            # The brain ran against a snapshot owned by `owner`; if that session
            # ended (or the room changed hands) while node was thinking, the plan
            # describes a coordinate system nobody holds — drop it.
            if owner is not self._room_owner or owner not in self._clients:
                log.info(f"plan [{trigger}] dropped: room owner left mid-plan")
                return

            self._plan_version += 1
            self._last_plan = plan
            self._last_planned_binding = planned_binding
            self._last_replan_trigger = trigger
            self._persist_plan_trace(trigger, brain_input_obj, plan)
            shopping = (plan.get("summary") or {}).get("shoppingLine", "")
            log.info(f"plan#{self._plan_version} [{trigger}] "
                     f"mode={plan.get('mode')} — {shopping}")
            # fused (which surface/object identity bound to + matchReason) and
            # target ({objectId, part} for part-addressed asks) ride along so
            # the phone's Truth Overlay can show the binding decision and the
            # runtime can land content on the part. Both optional — the phone
            # decodes them tolerantly.
            delta_payload = {
                "version": self._plan_version,
                "kind": "full",
                "experience": plan.get("contract"),
            }
            if plan.get("fused") is not None:
                delta_payload["fused"] = plan["fused"]
            if plan.get("target") is not None:
                delta_payload["target"] = plan["target"]
            delta = json.dumps({
                "type": "experience.delta",
                "sentAt": datetime.now(timezone.utc).isoformat(),
                "payload": delta_payload,
            })
            try:
                await owner.send(delta)
            except websockets.ConnectionClosed:
                self._clients.discard(owner)
                if owner is self._room_owner:
                    self._clear_room_state("room owner send failed")
        finally:
            self._inflight_bindings.discard(planned_binding)
            if trigger == "room.update":
                # Coalesced re-run: room.updates that landed while this plan
                # was queued/running collapse into ONE more replan against
                # the newest room — or the slot frees up.
                if (self._room_replan_dirty and self._room is not None
                        and self._room_owner in self._clients):
                    self._room_replan_dirty = False
                    self._spawn_replan(trigger="room.update")
                else:
                    self._room_replan_active = False
                    self._room_replan_dirty = False

    # -- frame uplink WebSocket -------------------------------------------

    async def handle_frame_ws(self, ws: WebSocketServerProtocol):
        peer = ws.remote_address
        self._clients.add(ws)
        # A fresh client must never inherit a room whose owner is gone (a PC
        # test client's sample room once poisoned a real phone session hours
        # later). A room with a LIVE owner is left alone — the owner keeps it.
        if self._room is not None and self._room_owner not in self._clients:
            self._clear_room_state(f"ownerless room found at connect of {peer}")
        log.info(f"phone connected (frame uplink) from {peer}")
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    self.submit_frame(msg)          # binary = a JPEG frame
                else:
                    # text = control channel (room.update → fusion replan)
                    await self._handle_control(ws, msg)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)
            if ws is self._room_owner:
                self._clear_room_state(f"room owner {peer} disconnected")
            log.info(f"phone disconnected (frame uplink) {peer}")

    # -- observability HTTP (the "see what it sees" view for Parsec) -------

    def state_dict(self) -> dict[str, Any]:
        return {
            "framesIn": self._frames_in,
            "inferences": self._infers,
            "ingestFps": round(self._ingest_fps, 2),
            "clients": len(self._clients),
            "result": self._result.to_dict() if self._result else None,
            "identificationAge": (round(time.time() - self._last_result_at, 2)
                                  if self._last_result_at else None),
            "roomSurfaces": len((self._room or {}).get("surfaces") or []),
            "objects": [
                {
                    "id": o.get("id"),
                    "label": o.get("label"),
                    "extents": (o.get("obb") or {}).get("extents"),
                    "supportSurfaceId": o.get("supportSurfaceId"),
                }
                for o in self._objects if isinstance(o, dict)
            ],
            "planVersion": self._plan_version,
            "planSummary": (self._last_plan or {}).get("summary"),
            "replan": {
                "lastTrigger": self._last_replan_trigger,
                "gate": {
                    "plannedBinding": _binding_str(self._last_planned_binding),
                    "candidateBinding": _binding_str(self._pending_binding),
                    "dwell": self._pending_count,
                    "dwellNeeded": self.replan_dwell,
                    "lastConfidence": round(self._last_conf, 3),
                    "minConfidence": self.replan_min_conf,
                },
            },
        }

    def build_http_app(self) -> web.Application:
        app = web.Application()

        async def index(_req):
            return web.Response(text=_DEBUG_HTML, content_type="text/html")

        async def frame_jpg(_req):
            if self._latest_frame is None:
                raise web.HTTPNotFound()
            return web.Response(body=self._latest_frame, content_type="image/jpeg",
                                headers={"Cache-Control": "no-store"})

        async def state_json(_req):
            return web.json_response(self.state_dict(),
                                     headers={"Cache-Control": "no-store"})

        async def contract_json(_req):
            # The brain's latest placement plan (mode + summary + contract) —
            # the web mirror and curl-based checks read this.
            if self._last_plan is None:
                raise web.HTTPNotFound()
            return web.json_response(self._last_plan,
                                     headers={"Cache-Control": "no-store"})

        app.router.add_get("/", index)
        app.router.add_get("/frame.jpg", frame_jpg)
        app.router.add_get("/state.json", state_json)
        app.router.add_get("/contract", contract_json)
        return app


def _binding_str(binding: Any) -> str | None:
    if binding is None:
        return None
    return "{}:{}".format(binding[0], binding[1] if binding[1] is not None else "?")


# Browser overlay: polls /state.json, draws /frame.jpg, overlays boxes+labels.
# No server-side image libs needed — the browser does the compositing, which is
# exactly what you watch over Parsec.
_DEBUG_HTML = """<!doctype html><html><head><meta charset=utf-8>
<title>SPATAIL vision engine</title>
<style>
 body{margin:0;background:#0c0c14;color:#e7e7ef;font:14px -apple-system,system-ui,sans-serif}
 header{padding:10px 14px;background:#15151f;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
 .k{color:#7c7c92}.v{color:#9db4ff;font-weight:600}
 #wrap{position:relative;display:inline-block;margin:14px}
 #frame{max-width:96vw;max-height:78vh;border-radius:10px;display:block;background:#000}
 #ov{position:absolute;left:0;top:0;pointer-events:none}
 #raw{padding:6px 14px;color:#6f6f86;white-space:pre-wrap;font-family:ui-monospace,monospace;font-size:12px}
 .panel{padding:4px 14px;color:#9a9ab0;white-space:pre-wrap;font-family:ui-monospace,monospace;font-size:12px}
 .dot{height:8px;width:8px;border-radius:50%;display:inline-block;margin-right:6px;background:#444}
 .live{background:#3ddc84}
</style></head><body>
<header>
 <span><span id="conn" class="dot"></span><b>SPATAIL Vision Engine</b></span>
 <span><span class="k">primary</span> <span id="primary" class="v">—</span></span>
 <span><span class="k">conf</span> <span id="conf" class="v">—</span></span>
 <span><span class="k">age</span> <span id="age" class="v">—</span></span>
 <span><span class="k">latency</span> <span id="lat" class="v">—</span></span>
 <span><span class="k">ingest</span> <span id="fps" class="v">—</span></span>
 <span><span class="k">infers</span> <span id="inf" class="v">—</span></span>
 <span><span class="k">clients</span> <span id="cli" class="v">—</span></span>
</header>
<div id="wrap"><img id="frame" src="/frame.jpg"><canvas id="ov"></canvas></div>
<div id="replan" class="panel"></div>
<div id="objects" class="panel"></div>
<div id="raw"></div>
<script>
const $=id=>document.getElementById(id);
const img=$('frame'),cv=$('ov'),cx=cv.getContext('2d');
function size(){cv.width=img.clientWidth;cv.height=img.clientHeight;}
img.onload=size;addEventListener('resize',size);
async function tick(){
 try{
  const s=await (await fetch('/state.json',{cache:'no-store'})).json();
  $('conn').className='dot live';
  $('fps').textContent=s.ingestFps+' fps';
  $('inf').textContent=s.inferences;
  $('cli').textContent=s.clients;
  $('age').textContent=(s.identificationAge==null)?'—':s.identificationAge.toFixed(1)+'s';
  const g=(s.replan&&s.replan.gate)||{};
  $('replan').textContent='replan  plan#'+(s.planVersion||0)
   +'  last='+((s.replan&&s.replan.lastTrigger)||'—')
   +'  |  gate: planned='+(g.plannedBinding||'—')
   +'  candidate='+(g.candidateBinding||'—')
   +'  dwell '+(g.dwell||0)+'/'+(g.dwellNeeded||0)
   +'  conf '+(g.lastConfidence!=null?g.lastConfidence:'—')+'≥'+(g.minConfidence!=null?g.minConfidence:'—');
  const objs=s.objects||[];
  $('objects').textContent='objects ('+objs.length+')'+(objs.length?':\\n':'')
   +objs.map(o=>'  '+(o.label||'(unlabeled)')
     +'  ['+((o.extents||[]).map(e=>(+e).toFixed(2)).join(' × ')||'?')+' m]'
     +'  on '+(o.supportSurfaceId||'—')).join('\\n');
  const r=s.result;
  if(r){
   $('primary').textContent=r.primary||'—';
   $('lat').textContent=(r.latencyMs||0)+' ms';
   const c=(r.detections[0]&&r.detections[0].confidence)||0;
   $('conf').textContent=(c*100).toFixed(0)+'%';
   $('raw').textContent=r.rawText||'';
   size();cx.clearRect(0,0,cv.width,cv.height);
   cx.lineWidth=3;cx.font='16px system-ui';cx.setLineDash([]);
   for(const d of r.detections){
    if(!d.box)continue;
    const[x,y,w,h]=d.box,X=x*cv.width,Y=y*cv.height,W=w*cv.width,H=h*cv.height;
    cx.strokeStyle='#3ddc84';cx.strokeRect(X,Y,W,H);
    cx.fillStyle='#3ddc84';const t=d.label+' '+(d.confidence*100).toFixed(0)+'%';
    const tw=cx.measureText(t).width;cx.fillRect(X,Y-20,tw+10,20);
    cx.fillStyle='#06210f';cx.fillText(t,X+5,Y-5);
   }
   // parts of the primary (spec §1.3) — same normalized space, dashed
   cx.lineWidth=2;cx.font='13px system-ui';cx.setLineDash([6,4]);
   for(const p of (r.parts||[])){
    if(!p.box)continue;
    const[x,y,w,h]=p.box,X=x*cv.width,Y=y*cv.height,W=w*cv.width,H=h*cv.height;
    cx.strokeStyle='#ffb347';cx.strokeRect(X,Y,W,H);
    cx.fillStyle='#ffb347';const t=p.label;
    const tw=cx.measureText(t).width;cx.fillRect(X,Y+H,tw+8,17);
    cx.fillStyle='#2b1a02';cx.fillText(t,X+4,Y+H+13);
   }
   cx.setLineDash([]);
  }
 }catch(e){$('conn').className='dot';}
 // bust the cache so the <img> pulls the newest frame
 img.src='/frame.jpg?t='+Date.now();
}
setInterval(tick,250);tick();
</script></body></html>"""


# ────────────────────────────────────────────────────────────────────────
# Test feeder — drive the engine from local images (no phone needed)
# ────────────────────────────────────────────────────────────────────────

async def test_feeder(engine: VisionEngine, test_dir: Path, fps: float):
    exts = {".jpg", ".jpeg", ".png"}
    images = sorted(p for p in test_dir.rglob("*") if p.suffix.lower() in exts)
    if not images:
        log.warning(f"--test-dir {test_dir} has no .jpg/.jpeg/.png; feeder idle")
        return
    log.info(f"test feeder cycling {len(images)} image(s) from {test_dir} at {fps} fps")
    # Note: PNGs are sent as-is; most OpenAI-compatible VLMs accept image/jpeg
    # OR image/png behind a data URL. If your server is strict, pre-convert to JPEG.
    i = 0
    while True:
        data = images[i % len(images)].read_bytes()
        engine.submit_frame(data)
        i += 1
        await asyncio.sleep(1.0 / max(fps, 0.1))


# ────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────

async def amain(args):
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    vlm = VLMAdapter(base_url=args.vlm_url, model=args.vlm_model,
                     api_key=args.vlm_key, max_tokens=args.max_tokens,
                     timeout_s=args.vlm_timeout)
    engine = VisionEngine(vlm, min_interval_s=args.min_interval)

    # observability HTTP (the Parsec view)
    http_runner = web.AppRunner(engine.build_http_app())
    await http_runner.setup()
    await web.TCPSite(http_runner, args.host, args.debug_port).start()
    log.info(f"debug view  →  http://{args.host}:{args.debug_port}/   (open over Parsec)")

    # frame uplink WebSocket (binary JPEG frames from the phone)
    ws_server = await websockets.serve(
        engine.handle_frame_ws, host=args.host, port=args.frame_port,
        # 16 MB (was 4): binary JPEG frames are small, but vision.focus crops
        # ride this socket as base64 TEXT (LIVE_BRAIN_SPEC §5.2) — an oversize
        # crop must reach the engine's graceful 8 MB reject, not die as a
        # websockets 1009 that would disconnect the phone mid-session.
        max_size=16 * 1024 * 1024,
        ping_interval=20, ping_timeout=20,
    )
    log.info(f"frame uplink →  ws://{args.host}:{args.frame_port}/v1/vision")
    log.info(f"VLM         →  {args.vlm_url}  (model: {args.vlm_model})")

    tasks = [asyncio.create_task(engine.run_worker())]
    if args.test_dir:
        tasks.append(asyncio.create_task(
            test_feeder(engine, Path(args.test_dir), args.test_fps)))

    log.info("Ready. Ctrl-C to stop.")
    try:
        await asyncio.gather(*tasks)
    finally:
        ws_server.close()
        await ws_server.wait_closed()
        await http_runner.cleanup()


def main():
    p = argparse.ArgumentParser(description="SPATAIL PC live vision intelligence engine")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--frame-port", type=int, default=8798, help="WS port for phone JPEG frames")
    p.add_argument("--debug-port", type=int, default=8799, help="HTTP port for the browser debug view")
    # Defaults target Ollama + Qwen2.5-VL — the fastest path to a working,
    # observable spike. Qwen2.5-VL also does object grounding (boxes) better
    # than most open VLMs. Swap --vlm-url/--vlm-model for NVIDIA NIM in prod.
    p.add_argument("--vlm-url", default="http://localhost:11434/v1",
                   help="OpenAI-compatible base URL (default: Ollama at :11434/v1; "
                        "use your NVIDIA NIM endpoint in production)")
    p.add_argument("--vlm-model", default="qwen2.5vl:7b",
                   help="model id (default: qwen2.5vl:7b)")
    p.add_argument("--vlm-key", default=None, help="bearer token if your endpoint needs one")
    # 8 s (LIVE_BRAIN_SPEC §1.4): a VLM that slow is a dead identification —
    # time it out and identify the newest frame instead of blocking the loop.
    p.add_argument("--vlm-timeout", type=float, default=8.0)
    # 450 matches the VLMAdapter default — the ambient reply now carries the
    # attributes dossier too (PERCEPTION_V3 §8) and 300 truncates mid-JSON.
    p.add_argument("--max-tokens", type=int, default=450)
    p.add_argument("--min-interval", type=float, default=0.4,
                   help="minimum seconds between VLM calls (GPU throttle)")
    p.add_argument("--test-dir", default=None,
                   help="feed images from this dir instead of (or alongside) the phone")
    p.add_argument("--test-fps", type=float, default=2.0)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
