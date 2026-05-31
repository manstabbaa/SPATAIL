"""job_server.py - SPATAIL generative AR job server (PC side).

Implements docs/generative_ar_contract.md exactly:

    POST /jobs            {prompt, client}      -> {id, status}
    GET  /jobs/{id}                              -> {id, status, stage, message,
                                                     usdz_url?, metadata_url?}
    GET  /artifacts/{file}                       -> USDZ / metadata bytes
    GET  /health                                 -> liveness + Blender bridge status

A single background worker serialises jobs (Blender is single-threaded): it drives
the LIVE Blender MCP bridge (localhost:9876) via generator.generate() to model +
animate the prompt and export a Y-up / metres / baked-looping USDZ into
studio/out/gen/, then flips the job to `done`.

Stdlib only - no pip installs. Run:

    python studio/server/job_server.py            # binds 0.0.0.0:8787

Binding 0.0.0.0 means the server answers on whatever interfaces exist, including
the Tailscale adapter once Tailscale is up - no firewall edits, no LAN assumptions
(Windows Firewall still blocks LAN inbound by default; Tailscale permits its own).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from urllib.parse import unquote, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_bridge  # noqa: E402
import generator  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]            # C:\SPATAIL_MAX
ARTIFACTS = ROOT / "studio" / "out" / "gen"
ARTIFACTS.mkdir(parents=True, exist_ok=True)

CONTRACT_VERSION = "0.1"

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
_QUEUE: "Queue[str]" = Queue()


def _update(job_id: str, **kw) -> None:
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(kw)


def _snapshot(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def _worker() -> None:
    """Serialise generation jobs FIFO; one Blender build at a time."""
    while True:
        job_id = _QUEUE.get()
        try:
            job = _snapshot(job_id)
            if not job:
                continue
            _update(job_id, status="running", stage="modeling", message=None)
            res = generator.generate(
                job["prompt"], job_id, ARTIFACTS,
                on_stage=lambda s, _id=job_id: _update(_id, stage=s),
            )
            _update(
                job_id, status="done", stage="ready", message=None,
                usdz=res["usdz_name"], metadata=res["metadata_name"],
                bbox_yup=res.get("bbox_yup"), max_dim=res.get("max_dim"),
                spec=res.get("spec"), finished=time.time(),
            )
            print(f"[job] {job_id} done -> {res['usdz_name']} "
                  f"(max_dim {res.get('max_dim')} m)", flush=True)
        except Exception as exc:  # noqa: BLE001
            _update(job_id, status="error", stage=None, message=str(exc),
                    finished=time.time())
            print(f"[job] {job_id} ERROR: {exc}", flush=True)
        finally:
            _QUEUE.task_done()


class Handler(BaseHTTPRequestHandler):
    server_version = "SpatailGenJobServer/0.1"

    # -- helpers --------------------------------------------------------------
    def _send(self, code: int, *, obj=None, raw: bytes | None = None,
              ctype: str = "application/json") -> None:
        body = raw if raw is not None else json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt, *args):  # concise access log
        print(f"[http] {self.address_string()} {fmt % args}", flush=True)

    # -- routes ---------------------------------------------------------------
    def do_POST(self):
        path = urlsplit(self.path).path
        if path != "/jobs":
            return self._send(404, obj={"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, obj={"error": "invalid JSON body"})
        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            return self._send(400, obj={"error": "missing 'prompt'"})
        job_id = "job_" + uuid.uuid4().hex[:8]
        with _LOCK:
            _JOBS[job_id] = {
                "id": job_id, "status": "queued", "stage": "queued",
                "message": None, "prompt": prompt,
                "client": data.get("client"), "created": time.time(),
                "usdz": None, "metadata": None,
            }
        _QUEUE.put(job_id)
        print(f"[job] {job_id} queued: {prompt!r}", flush=True)
        return self._send(200, obj={"id": job_id, "status": "queued"})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlsplit(self.path).path

        if path in ("/", "/health"):
            bridge = blender_bridge.ping()
            with _LOCK:
                njobs = len(_JOBS)
            return self._send(200, obj={
                "ok": True, "service": "spatail-gen-job-server",
                "contract": CONTRACT_VERSION,
                "blender_bridge": bool(bridge),
                "blender": (bridge or {}).get("blender"),
                "jobs": njobs, "artifacts_dir": str(ARTIFACTS),
            })

        if path.startswith("/jobs/"):
            job_id = unquote(path[len("/jobs/"):]).strip("/")
            job = _snapshot(job_id)
            if not job:
                return self._send(404, obj={"error": "unknown job", "id": job_id})
            out = {
                "id": job["id"], "status": job["status"],
                "stage": job.get("stage"), "message": job.get("message"),
            }
            if job["status"] == "done":
                out["usdz_url"] = f"/artifacts/{job['usdz']}"
                if job.get("metadata"):
                    out["metadata_url"] = f"/artifacts/{job['metadata']}"
            return self._send(200, obj=out)

        if path.startswith("/artifacts/"):
            fname = Path(unquote(path[len("/artifacts/"):])).name  # basename only
            fp = ARTIFACTS / fname
            if not fname or not fp.exists() or not fp.is_file():
                return self._send(404, obj={"error": "artifact not found", "file": fname})
            if fname.endswith(".usdz"):
                ctype = "model/vnd.usdz+zip"
            elif fname.endswith(".json"):
                ctype = "application/json"
            else:
                ctype = "application/octet-stream"
            body = fp.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'inline; filename="{fname}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return None

        return self._send(404, obj={"error": "not found"})


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main() -> None:
    ap = argparse.ArgumentParser(description="SPATAIL generative AR job server")
    ap.add_argument("--host", default="0.0.0.0",
                    help="bind address (default 0.0.0.0 = all interfaces incl. Tailscale)")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()

    threading.Thread(target=_worker, daemon=True, name="gen-worker").start()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True

    bridge = blender_bridge.ping()
    host_label = socket.gethostname().lower()
    print("=" * 64, flush=True)
    print(f"SPATAIL generative AR job server  (contract v{CONTRACT_VERSION})", flush=True)
    print(f"  bind            : {args.host}:{args.port}", flush=True)
    print(f"  local           : http://127.0.0.1:{args.port}", flush=True)
    print(f"  lan             : http://{_lan_ip()}:{args.port}", flush=True)
    print(f"  tailscale (MagicDNS, once up): http://{host_label}.<tailnet>.ts.net:{args.port}",
          flush=True)
    print(f"  artifacts       : {ARTIFACTS}", flush=True)
    print(f"  Blender bridge  : {'UP - ' + (bridge or {}).get('blender', '') if bridge else 'DOWN (open Blender + start MCP add-on)'}",
          flush=True)
    print("=" * 64, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] shutting down", flush=True)
        httpd.shutdown()


if __name__ == "__main__":
    main()
