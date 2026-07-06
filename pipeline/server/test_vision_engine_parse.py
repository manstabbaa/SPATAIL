#!/usr/bin/env python3
"""
test_vision_engine_parse.py — offline guards for the vision engine's parsing
and the Perception v3 additions (PERCEPTION_V3 §5/§7/§8, LIVE_BRAIN_SPEC §5):
attribute dossier cleaning, ambient/focus prompt shape, box normalization
(pixel xyxy → normalized xywh — the wire contract must not drift), and the
vision.focus → vision.focus.result path end-to-end with a fake VLM.

Run:  python3 pipeline/server/test_vision_engine_parse.py

No pytest, no network, no phone. The engine imports websockets/aiohttp at
module top; when this Mac doesn't have them, minimal stubs are injected so the
pure parsing code stays testable anywhere.
"""
import asyncio
import base64
import importlib
import json
import logging
import sys
import types
from pathlib import Path


# ── engine import (with dependency stubs when needed) ────────────────────

def _install_stubs():
    try:
        import websockets  # noqa: F401
    except ImportError:
        ws = types.ModuleType("websockets")

        class ConnectionClosed(Exception):
            pass

        class WebSocketServerProtocol:  # annotation-only in the engine
            pass

        server = types.ModuleType("websockets.server")
        server.WebSocketServerProtocol = WebSocketServerProtocol
        ws.ConnectionClosed = ConnectionClosed
        ws.server = server
        sys.modules["websockets"] = ws
        sys.modules["websockets.server"] = server
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        aio = types.ModuleType("aiohttp")

        class ClientTimeout:
            def __init__(self, total=None):
                self.total = total

        class ClientSession:
            def __init__(self, *a, **k):
                self.closed = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                self.closed = True
                return False

            async def close(self):
                self.closed = True

            def post(self, *a, **k):
                raise RuntimeError("stub ClientSession: no network in tests")

        web = types.ModuleType("aiohttp.web")

        class Application:
            def __init__(self, *a, **k):
                pass

        web.Application = Application
        aio.ClientTimeout = ClientTimeout
        aio.ClientSession = ClientSession
        aio.web = web
        sys.modules["aiohttp"] = aio
        sys.modules["aiohttp.web"] = web


def _load_engine():
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        return importlib.import_module("spatail_vision_engine")
    except (ImportError, SystemExit):
        # the engine converts missing deps into SystemExit — stub and retry
        _install_stubs()
        sys.modules.pop("spatail_vision_engine", None)
        return importlib.import_module("spatail_vision_engine")


ENG = _load_engine()
logging.getLogger("spatail.vision").setLevel(logging.CRITICAL)  # quiet rejects


# ── tiny check harness ────────────────────────────────────────────────────

PASS = 0
FAILED: list[str] = []


def check(name: str, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAILED.append(name)
        print(f"FAIL  {name}" + (f"  — {detail}" if detail else ""))


def section(fn):
    try:
        fn()
    except Exception as exc:  # a crash counts as a failure, not an abort
        FAILED.append(fn.__name__)
        print(f"FAIL  {fn.__name__} raised {exc!r}")


# ── fixtures ──────────────────────────────────────────────────────────────

# Minimal JPEG: SOI + SOF0 (height 64, width 80) + EOI — enough for
# _image_size's header scan; nothing in these tests decodes pixels.
TINY_JPEG = bytes([
    0xFF, 0xD8,
    0xFF, 0xC0, 0x00, 0x11, 0x08,
    0x00, 0x40,              # height 64
    0x00, 0x50,              # width 80
    0x03, 0x01, 0x22, 0x00, 0x02, 0x11, 0x01, 0x03, 0x11, 0x01,
    0xFF, 0xD9,
])

OLD_REPLY = json.dumps({
    "primary": "water bottle", "confidence": 0.85,
    "alternatives": [{"label": "flask", "confidence": 0.4}],
    "box": [100, 50, 600, 300],
    "parts": [{"label": "cap", "box": [200, 50, 400, 120], "confidence": 0.8}],
})

ATTR_REPLY = json.dumps({
    "primary": "water bottle", "confidence": 0.85,
    "alternatives": [{"label": "flask", "confidence": 0.4}],
    "box": [100, 50, 600, 300],
    "parts": [{"label": "cap", "box": [200, 50, 400, 120], "confidence": 0.8}],
    "attributes": {"colors": ["blue", "black"], "text_content": "QWERTY",
                   "language": "English", "brand": "", "state": "on"},
})

FOCUS_REPLY = json.dumps({
    "primary": "mechanical keyboard", "confidence": 0.92,
    "attributes": {"colors": ["blue", "black"], "text_content": ["QWERTY"],
                   "language": "English"},
    "answer": "The keycaps are labeled in English.",
    "parts": [{"label": "escape key", "box": [8, 16, 24, 48]}],
})


class FakeVLM:
    """Mimics VLMAdapter.identify's parse path on a canned reply — the same
    _parse_identify_json + _image_size machinery, no HTTP."""
    model = "fake-vlm"

    def __init__(self, reply_text):
        self.reply_text = reply_text
        self.prompts: list = []

    async def identify(self, jpeg, http, prompt=None):
        self.prompts.append(prompt)
        frame_size = ENG._image_size(jpeg)
        dets, primary, parts, attrs = ENG._parse_identify_json(self.reply_text, frame_size)
        return ENG.IdentifyResult(dets, primary, self.reply_text, 123, self.model,
                                  parts=parts, attributes=attrs, frame_size=frame_size)


class FakeWS:
    remote_address = ("test-client", 0)

    def __init__(self):
        self.sent: list = []

    async def send(self, msg):
        self.sent.append(msg)


def focus_msg(request_id, object_id, jpeg_b64, question=None, wanted=None):
    payload = {"requestId": request_id, "objectId": object_id,
               "jpegBase64": jpeg_b64, "frameTimestamp": 1783036800.123}
    if question is not None:
        payload["question"] = question
    if wanted is not None:
        payload["wanted"] = wanted
    return json.dumps({"type": "vision.focus", "payload": payload})


# ── tests ─────────────────────────────────────────────────────────────────

def test_image_size():
    check("image_size: tiny jpeg", ENG._image_size(TINY_JPEG) == (80, 64),
          repr(ENG._image_size(TINY_JPEG)))
    check("image_size: junk → None", ENG._image_size(b"not a jpeg") is None)


def test_clean_attributes():
    f = ENG._clean_attributes
    check("attrs: non-dict → None", f(None) is None and f("blue") is None and f([1]) is None)
    check("attrs: empty dict → None", f({}) is None)
    check("attrs: empty list → None", f({"colors": []}) is None)
    check("attrs: non-string items → None", f({"colors": [1, 2]}) is None)
    check("attrs: bare string → 1-list", f({"colors": "red"}) == {"colors": ["red"]})
    got = f({"colors": ["a" * 60]})
    check("attrs: item clipped to 48", got == {"colors": ["a" * 48]}, repr(got))
    got = f({"colors": [f"c{i}" for i in range(10)]})
    check("attrs: list capped at 6", got == {"colors": [f"c{i}" for i in range(6)]}, repr(got))
    check("attrs: textContent alias in → camelCase out",
          f({"textContent": ["QWERTY"]}) == {"textContent": ["QWERTY"]})
    check("attrs: snake text_content in → camelCase out",
          f({"text_content": "hi"}) == {"textContent": ["hi"]})
    check("attrs: scalar stripped", f({"language": "  English  "}) == {"language": "English"})
    check("attrs: scalar 1-list coerced", f({"state": ["open"]}) == {"state": "open"})
    got = f({"brand": "x" * 80})
    check("attrs: scalar clipped to 48", got == {"brand": "x" * 48}, repr(got))
    check("attrs: empty scalar dropped → None", f({"brand": "", "state": "  "}) is None)
    got = f({"colors": ["blue"], "materials": ["plastic", ""],
             "language": "English", "bogus": "junk"})
    check("attrs: unknown keys dropped",
          got == {"colors": ["blue"], "materials": ["plastic"], "language": "English"},
          repr(got))


def test_parse_attributes_reply():
    dets, primary, parts, attrs = ENG._parse_identify_json(ATTR_REPLY, (1000, 500))
    check("parse+attrs: primary", primary == "water bottle")
    check("parse+attrs: det count", len(dets) == 2, repr(dets))
    check("parse+attrs: primary box normalized exactly",
          dets[0].box == [0.1, 0.1, 0.5, 0.5], repr(dets[0].box))
    check("parse+attrs: part box normalized exactly",
          parts == [{"label": "cap", "box": [0.2, 0.1, 0.2, 0.14], "confidence": 0.8}],
          repr(parts))
    check("parse+attrs: dossier cleaned (brand dropped, alias out camelCase)",
          attrs == {"colors": ["blue", "black"], "textContent": ["QWERTY"],
                    "language": "English", "state": "on"}, repr(attrs))
    # fenced reply still parses
    _, _, _, attrs2 = ENG._parse_identify_json("```json\n" + ATTR_REPLY + "\n```", (1000, 500))
    check("parse+attrs: code fence tolerated", attrs2 == attrs, repr(attrs2))
    # frame_size None: pixel boxes drop, identity + dossier survive
    dets3, primary3, parts3, attrs3 = ENG._parse_identify_json(ATTR_REPLY, None)
    check("parse+attrs: no frame size → boxes dropped, rest intact",
          primary3 == "water bottle" and dets3[0].box is None
          and parts3[0]["box"] is None and attrs3 == attrs,
          f"{dets3[0].box} {parts3[0]['box']} {attrs3}")


def test_parse_regression_no_attributes():
    dets, primary, parts, attrs = ENG._parse_identify_json(OLD_REPLY, (1000, 500))
    check("regression: primary", primary == "water bottle")
    check("regression: confidences", [d.confidence for d in dets] == [0.85, 0.4])
    check("regression: primary box", dets[0].box == [0.1, 0.1, 0.5, 0.5], repr(dets[0].box))
    check("regression: alt box None", dets[1].box is None)
    check("regression: parts identical",
          parts == [{"label": "cap", "box": [0.2, 0.1, 0.2, 0.14], "confidence": 0.8}],
          repr(parts))
    check("regression: attributes None", attrs is None, repr(attrs))
    # normalized xywh passthrough (models that DO obey) — _clean_box contract
    d2, _, _, _ = ENG._parse_identify_json(
        json.dumps({"primary": "cup", "confidence": 0.5, "box": [0.1, 0.2, 0.3, 0.4]}), None)
    check("regression: normalized box passthrough", d2[0].box == [0.1, 0.2, 0.3, 0.4],
          repr(d2[0].box))
    # prose fallback unchanged, now with attrs None
    dets3, primary3, parts3, attrs3 = ENG._parse_identify_json("a red stapler.", None)
    check("regression: prose fallback",
          primary3 == "a red stapler" and dets3[0].confidence == 0.3
          and parts3 == [] and attrs3 is None,
          f"{primary3!r} {parts3} {attrs3}")


def test_identify_result_to_dict():
    r = ENG.IdentifyResult([], None, "raw", 10, "m", attributes={"colors": ["red"]})
    check("to_dict: attributes ride the wire",
          r.to_dict().get("attributes") == {"colors": ["red"]})
    r2 = ENG.IdentifyResult([], None, "raw", 10, "m")
    check("to_dict: attributes default None",
          "attributes" in r2.to_dict() and r2.to_dict()["attributes"] is None)


def test_ambient_prompt_shape():
    p = ENG._IDENTIFY_PROMPT
    check("ambient prompt: requests attributes",
          '"attributes"' in p and '"text_content"' in p and '"language"' in p
          and '"brand"' in p and '"state"' in p)
    check("ambient prompt: primary-only + confident-only instruction",
          "PRIMARY object only" in p and "confident" in p)
    check("ambient prompt: existing format intact",
          '"primary"' in p and '"alternatives"' in p
          and '"box": [x1, y1, x2, y2]' in p and '"parts"' in p
          and "image pixels" in p)


def test_focus_prompt():
    q = "what language are the keys?"
    p1 = ENG._focus_prompt(q, ["textContent", "colors"])
    check("focus prompt: contains question", q in p1)
    check("focus prompt: wanted fields (camelCase in → snake shape)",
          '"text_content"' in p1 and '"colors"' in p1)
    # quoted forms: the bare word "brand" rides the identity instruction
    # ("brand/model if readable") by design — only the JSON shape must shrink
    check("focus prompt: unwanted fields absent from the JSON shape",
          '"materials"' not in p1 and '"brand"' not in p1)
    check("focus prompt: answer requested with question", '"answer"' in p1)
    check("focus prompt: identity-first + parts pixel boxes",
          "Identify the object" in p1 and '"parts"' in p1
          and "[x1, y1, x2, y2]" in p1)
    p2 = ENG._focus_prompt(None, None)
    check("focus prompt: no question → no answer field", '"answer"' not in p2)
    check("focus prompt: no wanted → full dossier shape",
          all(f'"{k}"' in p2 for k in
              ("colors", "materials", "text_content", "language", "brand", "state")))
    p3 = ENG._focus_prompt("", ["bogus", 7])
    check("focus prompt: junk wanted → full shape, empty question → no answer",
          '"colors"' in p3 and '"answer"' not in p3 and "bogus" not in p3)


def test_extract_answer():
    check("answer: extracted", ENG._extract_answer(FOCUS_REPLY)
          == "The keycaps are labeled in English.")
    check("answer: absent → None", ENG._extract_answer(OLD_REPLY) is None)
    check("answer: no json → None", ENG._extract_answer("<error: timeout>") is None)
    check("answer: number stringified",
          ENG._extract_answer('{"answer": 87}') == "87")
    long = json.dumps({"answer": "a" * 500})
    check("answer: clipped to 300", ENG._extract_answer(long) == "a" * 300)


def test_focus_end_to_end():
    async def scenario():
        vlm = FakeVLM(FOCUS_REPLY)
        engine = ENG.VisionEngine(vlm)   # built inside the loop (py3.9 locks)
        ws = FakeWS()
        b64 = base64.b64encode(TINY_JPEG).decode("ascii")
        await engine._handle_control(ws, focus_msg(
            "f-1", "obj-9", b64, question="what language are the keys?",
            wanted=["textContent", "language", "colors"]))
        await asyncio.gather(*list(engine._focus_tasks))
        # second pass: crop whose header can't be sized → frame_size None,
        # parse path must still deliver identity/attrs with boxes dropped
        await engine._handle_control(ws, focus_msg(
            "n-1", "obj-9", base64.b64encode(b"not really a jpeg").decode("ascii")))
        await asyncio.gather(*list(engine._focus_tasks))
        return vlm, ws

    vlm, ws = asyncio.run(scenario())
    check("focus e2e: two replies", len(ws.sent) == 2, f"got {len(ws.sent)}")
    if len(ws.sent) < 2:
        return
    env = json.loads(ws.sent[0])
    check("focus e2e: envelope type", env.get("type") == "vision.focus.result")
    check("focus e2e: sentAt iso", isinstance(env.get("sentAt"), str) and env["sentAt"])
    p = env.get("payload") or {}
    check("focus e2e: ids echoed",
          p.get("requestId") == "f-1" and p.get("objectId") == "obj-9", repr(p))
    check("focus e2e: label + confidence",
          p.get("label") == "mechanical keyboard" and p.get("confidence") == 0.92,
          f"{p.get('label')!r} {p.get('confidence')!r}")
    check("focus e2e: attributes camelCase",
          p.get("attributes") == {"colors": ["blue", "black"],
                                  "textContent": ["QWERTY"], "language": "English"},
          repr(p.get("attributes")))
    check("focus e2e: answer", p.get("answer") == "The keycaps are labeled in English.")
    check("focus e2e: parts normalized against the 80x64 CROP",
          p.get("parts") == [{"label": "escape key", "box": [0.1, 0.25, 0.2, 0.5]}],
          repr(p.get("parts")))
    check("focus e2e: latency/model/rawText",
          p.get("latencyMs") == 123 and p.get("model") == "fake-vlm"
          and p.get("rawText") == FOCUS_REPLY)
    prompt = vlm.prompts[0]
    check("focus e2e: prompt question-conditioned",
          "what language are the keys?" in prompt and '"answer"' in prompt)
    check("focus e2e: prompt wanted-conditioned",
          '"text_content"' in prompt and '"colors"' in prompt
          and '"materials"' not in prompt)
    p2 = (json.loads(ws.sent[1]).get("payload")) or {}
    check("focus e2e: unsizable crop → boxes dropped, identity kept",
          p2.get("requestId") == "n-1" and p2.get("label") == "mechanical keyboard"
          and p2.get("parts") == [{"label": "escape key", "box": None}],
          repr(p2.get("parts")))


def test_focus_latest_wins():
    async def scenario():
        vlm = FakeVLM(FOCUS_REPLY)
        engine = ENG.VisionEngine(vlm)
        ws = FakeWS()
        b64 = base64.b64encode(TINY_JPEG).decode("ascii")
        # two asks for the SAME object before the first pass starts —
        # the queued one is replaced (latest-wins), one VLM call total
        await engine._handle_control(ws, focus_msg("f-1", "obj-9", b64, question="first thing?"))
        await engine._handle_control(ws, focus_msg("f-2", "obj-9", b64, question="second thing?"))
        await asyncio.gather(*list(engine._focus_tasks))
        return vlm, ws

    vlm, ws = asyncio.run(scenario())
    check("latest-wins: one reply", len(ws.sent) == 1, f"got {len(ws.sent)}")
    check("latest-wins: one VLM call", len(vlm.prompts) == 1, f"got {len(vlm.prompts)}")
    if not ws.sent:
        return
    p = json.loads(ws.sent[0]).get("payload") or {}
    check("latest-wins: newest request answered", p.get("requestId") == "f-2", repr(p))
    check("latest-wins: newest question rode along",
          vlm.prompts and "second thing?" in vlm.prompts[0])


def test_focus_error_replies():
    async def scenario():
        vlm = FakeVLM(FOCUS_REPLY)
        engine = ENG.VisionEngine(vlm)
        ws = FakeWS()
        await engine._handle_control(ws, focus_msg("e-1", "obj-1", "!!!not base64!!!"))
        big = base64.b64encode(
            b"\xff" * (ENG.VisionEngine._FOCUS_MAX_JPEG + 1)).decode("ascii")
        await engine._handle_control(ws, focus_msg("e-2", "obj-1", big))
        await engine._handle_control(ws, json.dumps({
            "type": "vision.focus",
            "payload": {"requestId": "e-3", "objectId": "obj-1"}}))  # no jpeg
        await engine._handle_control(ws, json.dumps({"type": "vision.focus"}))  # no payload
        await asyncio.gather(*list(engine._focus_tasks))
        return vlm, ws

    vlm, ws = asyncio.run(scenario())
    check("focus errors: four replies, zero VLM calls",
          len(ws.sent) == 4 and vlm.prompts == [],
          f"sent={len(ws.sent)} calls={len(vlm.prompts)}")
    for i, (rid, needle) in enumerate([("e-1", "undecodable"), ("e-2", "too large"),
                                       ("e-3", "missing"), (None, "missing")]):
        if i >= len(ws.sent):
            break
        p = json.loads(ws.sent[i]).get("payload") or {}
        check(f"focus errors[{i}]: error-shaped reply",
              p.get("requestId") == rid and p.get("answer") is None
              and p.get("label") is None and p.get("attributes") is None
              and p.get("parts") == [] and str(p.get("rawText", "")).startswith("<error:")
              and needle in str(p.get("rawText")),
              repr(p.get("rawText")))


# ── run ───────────────────────────────────────────────────────────────────

def main():
    for fn in (test_image_size, test_clean_attributes, test_parse_attributes_reply,
               test_parse_regression_no_attributes, test_identify_result_to_dict,
               test_ambient_prompt_shape, test_focus_prompt, test_extract_answer,
               test_focus_end_to_end, test_focus_latest_wins, test_focus_error_replies):
        section(fn)
    print(f"\n{PASS} passed, {len(FAILED)} failed")
    if FAILED:
        for name in FAILED:
            print(f"  failed: {name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
