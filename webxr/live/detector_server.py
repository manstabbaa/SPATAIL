"""detector_server.py — SPATAIL live perception layer.

A small CORS-enabled HTTP server that turns a camera frame into detections the
WebXR viewer renders through its identify/hitbox overlay. This is the real
"live running intelligence from a video feed": the browser posts a webcam frame,
Gemini returns labeled 2D boxes, and we hand them back in the viewer's detection
shape. Reuses the Gemini REST + strict-JSON pattern from studio/director/vision.py
and studio/vision/review_gemini.py.

Run:  python webxr/live/detector_server.py [--port 8766]
Key:  GEMINI_API_KEY in env or ~/.spatail/secrets.env
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_SECRETS = Path.home() / ".spatail" / "secrets.env"
MODEL = os.environ.get("SPATAIL_VISION_MODEL", "gemini-2.5-flash")
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

DETECT_PROMPT = """You are SPATAIL's live perception layer. A user is looking at a real scene
through a camera. Detect the salient real-world objects and named PARTS they might want to
understand, explain, repair, compare, or interact with. Prefer specific named components over
generic ones (e.g. "intake manifold" not just "engine"). Return STRICT JSON only:
{"detections":[{"label":"<short noun phrase>","confidence":<0..1>,
  "intent_hint":"<one of: explain|repair|compare|recognize|place>",
  "box_2d":[ymin,xmin,ymax,xmax]}]}
box_2d is the bounding box normalized to 0-1000 (origin top-left). Return at most 8, most
salient first. No prose, no markdown."""


def _key() -> str:
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    if _SECRETS.exists():
        for line in _SECRETS.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("GEMINI_API_KEY not set (env or ~/.spatail/secrets.env)")


def _gemini(parts, *, timeout=60) -> dict:
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
    last = "unknown"
    for _ in range(3):
        try:
            req = urllib.request.Request(API.format(model=MODEL, key=_key()),
                                         data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            data = json.load(urllib.request.urlopen(req, timeout=timeout))
            txt = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            return json.loads(txt)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.read()[:160].decode('utf-8', 'ignore')!r}"
            if e.code in (429, 500, 503):
                time.sleep(2); continue
            raise RuntimeError(f"gemini detect failed — {last}")
        except Exception as e:  # noqa: BLE001
            last = repr(e)[:200]; time.sleep(1)
    raise RuntimeError(f"gemini detect failed — {last}")


def _project_3d(cx, cy, w01, h01, *, depth=0.7, fov_deg=55.0, aspect=1.5):
    """Coarse monocular → world projection so detections also show in the 3D overlay.
    Honest caveat: depth is assumed (a webcam has no depth); on a headset with scene
    depth this becomes exact. The 2D-over-video boxes are the truthful live view."""
    import math
    ndcx = cx * 2 - 1
    ndcy = (1 - cy) * 2 - 1               # flip y (image top = +screen)
    half_h = depth * math.tan(math.radians(fov_deg) / 2)
    half_w = half_h * aspect
    x = ndcx * half_w
    y = 1.1 + ndcy * half_h               # ~eye height
    z = 0.6 - depth                       # in front of the room origin
    sx = max(0.03, w01 * half_w * 2)
    sy = max(0.03, h01 * half_h * 2)
    return [round(x, 3), round(y, 3), round(z, 3)], [round(sx, 3), round(sy, 3), round((sx + sy) / 2, 3)]


def detect(image_b64: str, mime: str = "image/jpeg") -> list[dict]:
    parts = [{"text": DETECT_PROMPT},
             {"inlineData": {"mimeType": mime, "data": image_b64}}]
    raw = _gemini(parts)
    out = []
    for d in raw.get("detections", [])[:8]:
        b = d.get("box_2d") or [0, 0, 0, 0]
        if len(b) != 4:
            continue
        ymin, xmin, ymax, xmax = [max(0.0, min(1000.0, float(v))) / 1000.0 for v in b]
        x, y, w, h = xmin, ymin, max(0.0, xmax - xmin), max(0.0, ymax - ymin)
        pos, size = _project_3d(x + w / 2, y + h / 2, w, h)
        out.append({
            "label": str(d.get("label", "object"))[:60],
            "confidence": round(float(d.get("confidence", 0.5)), 2),
            "intent_hint": d.get("intent_hint", "recognize"),
            "source": f"gemini:{MODEL}",
            "bbox": [round(x, 4), round(y, 4), round(w, 4), round(h, 4)],   # 2D, normalized 0..1
            "position": pos, "sizeMeters": size,                            # coarse 3D
        })
    return out


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        if self.path.startswith("/health"):
            ok = True; note = ""
            try: _key()
            except Exception as e: ok = False; note = str(e)
            return self._json(200, {"ok": ok, "model": MODEL, "note": note})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/detect"):
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
            img = payload.get("imageBase64", "")
            if img.startswith("data:"):                      # tolerate data URLs
                img = img.split(",", 1)[1]
            if not img:
                return self._json(400, {"error": "imageBase64 required"})
            t0 = time.time()
            dets = detect(img, payload.get("mime", "image/jpeg"))
            return self._json(200, {"detections": dets, "ms": int((time.time() - t0) * 1000),
                                    "model": MODEL})
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": str(e)})

    def log_message(self, fmt, *args):   # quieter logs
        sys.stderr.write("[detector] " + (fmt % args) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--self-test", metavar="IMAGE", help="run detection on an image file and print JSON")
    args = ap.parse_args()
    if args.self_test:
        b = Path(args.self_test).read_bytes()
        mime = "image/png" if args.self_test.lower().endswith(".png") else "image/jpeg"
        print(json.dumps(detect(base64.b64encode(b).decode(), mime), indent=2))
        return
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"[detector] SPATAIL live perception on :{args.port} (model {MODEL})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
