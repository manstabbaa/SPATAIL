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
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
    from aiohttp import web, ClientSession, ClientTimeout
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependencies. Install with:\n"
        "    pip install websockets aiohttp\n"
        f"Original error: {exc}"
    )

log = logging.getLogger("spatail.vision")


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "detections": [d.to_dict() for d in self.detections],
            "primary": self.primary,
            "rawText": self.raw_text,
            "latencyMs": self.latency_ms,
            "model": self.model,
        }


# ────────────────────────────────────────────────────────────────────────
# VLM adapter — OpenAI-compatible vision chat (NIM / Ollama / vLLM)
# ────────────────────────────────────────────────────────────────────────

_IDENTIFY_PROMPT = (
    "You are the vision system of an AR teaching app. Look at the image and "
    "identify the single most prominent real-world OBJECT the user is pointing "
    "their camera at (ignore background, hands, walls, floor). "
    "Respond with ONLY a JSON object, no prose, of the form:\n"
    '{"primary": "<short noun, e.g. \\"car engine air filter\\">", '
    '"confidence": <0..1>, '
    '"alternatives": [{"label": "<noun>", "confidence": <0..1>}], '
    '"box": [x, y, w, h] }\n'
    "box is the normalized bounding box of the primary object in 0..1 "
    "(origin top-left); use null if unsure. Keep labels concrete and specific."
)


class VLMAdapter:
    """Calls an OpenAI-compatible /chat/completions endpoint with an image.

    Works against NVIDIA NIM, Ollama, and vLLM out of the box. Override
    `identify` for a native API that returns real detection boxes.
    """

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 prompt: str = _IDENTIFY_PROMPT, timeout_s: float = 30.0,
                 max_tokens: int = 300):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.prompt = prompt
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens

    async def identify(self, jpeg: bytes, http: ClientSession) -> IdentifyResult:
        data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0.0,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": self.prompt},
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
            log.warning(f"VLM call failed in {latency}ms: {exc}")
            return IdentifyResult([], None, f"<error: {exc}>", latency, self.model)

        latency = int((time.monotonic() - t0) * 1000)
        dets, primary = _parse_identify_json(text)
        return IdentifyResult(dets, primary, text, latency, self.model)


def _parse_identify_json(text: str) -> tuple[list[Detection], str | None]:
    """Best-effort extraction of detections from a chat VLM reply.

    VLMs wrap JSON in code fences or prose; pull the first {...} blob. If it
    can't be parsed at all, fall back to treating the whole reply as one label.
    """
    blob = None
    fence = re.search(r"\{.*\}", text, re.DOTALL)
    if fence:
        try:
            blob = json.loads(fence.group(0))
        except json.JSONDecodeError:
            blob = None
    if blob is None:
        label = text.strip().strip(".")[:80]
        return ([Detection(label=label, confidence=0.3)] if label else []), (label or None)

    dets: list[Detection] = []
    primary = blob.get("primary") or blob.get("label")
    if primary:
        dets.append(Detection(
            label=str(primary),
            confidence=float(blob.get("confidence", 0.0) or 0.0),
            box=_clean_box(blob.get("box")),
        ))
    for alt in (blob.get("alternatives") or []):
        if isinstance(alt, dict) and alt.get("label"):
            dets.append(Detection(label=str(alt["label"]),
                                  confidence=float(alt.get("confidence", 0.0) or 0.0),
                                  box=_clean_box(alt.get("box"))))
    return dets, (str(primary) if primary else None)


def _clean_box(box: Any) -> list[float] | None:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        vals = [float(v) for v in box]
    except (TypeError, ValueError):
        return None
    # Heuristic: if values look like pixels (>1.5), we can't normalize without
    # the source size, so drop rather than mislead the overlay.
    if any(v > 1.5 for v in vals):
        return None
    return vals


# ────────────────────────────────────────────────────────────────────────
# Vision engine — frame ingest + single-flight VLM worker + observability
# ────────────────────────────────────────────────────────────────────────

class VisionEngine:
    def __init__(self, vlm: VLMAdapter, min_interval_s: float = 0.4):
        self.vlm = vlm
        self.min_interval_s = min_interval_s  # don't hammer the VLM faster than this

        self._latest_frame: bytes | None = None
        self._frame_ready = asyncio.Event()
        self._result: IdentifyResult | None = None
        self._clients: set[WebSocketServerProtocol] = set()

        # rolling stats for the debug view
        self._frames_in = 0
        self._infers = 0
        self._last_frame_at = 0.0
        self._ingest_fps = 0.0

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
                if frame is None:
                    continue
                result = await self.vlm.identify(frame, http)
                self._infers += 1
                self._result = result
                await self._broadcast(result)
                log.info(
                    f"id#{self._infers} {result.latency_ms}ms "
                    f"primary={result.primary!r} "
                    f"({len(result.detections)} det, ingest {self._ingest_fps:.1f}fps)"
                )
                # throttle so we don't melt the GPU on a fast frame stream
                await asyncio.sleep(self.min_interval_s)

    async def _broadcast(self, result: IdentifyResult):
        if not self._clients:
            return
        msg = json.dumps({
            "type": "vision.identification",
            "sentAt": datetime.now(timezone.utc).isoformat(),
            "payload": result.to_dict(),
        })
        dead = []
        for ws in self._clients:
            try:
                await ws.send(msg)
            except websockets.ConnectionClosed:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    # -- frame uplink WebSocket -------------------------------------------

    async def handle_frame_ws(self, ws: WebSocketServerProtocol):
        peer = ws.remote_address
        self._clients.add(ws)
        log.info(f"phone connected (frame uplink) from {peer}")
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    self.submit_frame(msg)          # binary = a JPEG frame
                else:
                    # text control messages reserved for future use (e.g. ROI)
                    log.debug(f"frame-ws text: {msg[:120]}")
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)
            log.info(f"phone disconnected (frame uplink) {peer}")

    # -- observability HTTP (the "see what it sees" view for Parsec) -------

    def state_dict(self) -> dict[str, Any]:
        return {
            "framesIn": self._frames_in,
            "inferences": self._infers,
            "ingestFps": round(self._ingest_fps, 2),
            "clients": len(self._clients),
            "result": self._result.to_dict() if self._result else None,
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

        app.router.add_get("/", index)
        app.router.add_get("/frame.jpg", frame_jpg)
        app.router.add_get("/state.json", state_json)
        return app


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
 .dot{height:8px;width:8px;border-radius:50%;display:inline-block;margin-right:6px;background:#444}
 .live{background:#3ddc84}
</style></head><body>
<header>
 <span><span id="conn" class="dot"></span><b>SPATAIL Vision Engine</b></span>
 <span><span class="k">primary</span> <span id="primary" class="v">—</span></span>
 <span><span class="k">conf</span> <span id="conf" class="v">—</span></span>
 <span><span class="k">latency</span> <span id="lat" class="v">—</span></span>
 <span><span class="k">ingest</span> <span id="fps" class="v">—</span></span>
 <span><span class="k">infers</span> <span id="inf" class="v">—</span></span>
 <span><span class="k">clients</span> <span id="cli" class="v">—</span></span>
</header>
<div id="wrap"><img id="frame" src="/frame.jpg"><canvas id="ov"></canvas></div>
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
  const r=s.result;
  if(r){
   $('primary').textContent=r.primary||'—';
   $('lat').textContent=(r.latencyMs||0)+' ms';
   const c=(r.detections[0]&&r.detections[0].confidence)||0;
   $('conf').textContent=(c*100).toFixed(0)+'%';
   $('raw').textContent=r.rawText||'';
   size();cx.clearRect(0,0,cv.width,cv.height);
   cx.lineWidth=3;cx.font='16px system-ui';
   for(const d of r.detections){
    if(!d.box)continue;
    const[x,y,w,h]=d.box,X=x*cv.width,Y=y*cv.height,W=w*cv.width,H=h*cv.height;
    cx.strokeStyle='#3ddc84';cx.strokeRect(X,Y,W,H);
    cx.fillStyle='#3ddc84';const t=d.label+' '+(d.confidence*100).toFixed(0)+'%';
    const tw=cx.measureText(t).width;cx.fillRect(X,Y-20,tw+10,20);
    cx.fillStyle='#06210f';cx.fillText(t,X+5,Y-5);
   }
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
        max_size=4 * 1024 * 1024,   # frames are big; control plane stays separate
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
    p.add_argument("--vlm-timeout", type=float, default=30.0)
    p.add_argument("--max-tokens", type=int, default=300)
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
