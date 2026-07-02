"""Tiny static server for the studio viewer. Serves the repo root so the page
can fetch /studio/out/StudioSceneContract.json, /studio/out/studio.glb and
/studio/viewer/*. Zero dependencies (stdlib only).

    python studio/viewer/server.py        # http://localhost:5180/studio/viewer/studio.html
"""
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repo root
STUDIO = ROOT / "studio"
PORT = int(os.environ.get("STUDIO_PORT", "5180"))

# one build at a time; the front door is single-user in the tester room
_BUILD_LOCK = threading.Lock()
_LAST = {"status": "idle", "question": None, "log": ""}


def _run_educator(question: str):
    """Run the EDUCATOR pipeline for a question. Returns (ok, log)."""
    with _BUILD_LOCK:
        _LAST.update(status="building", question=question, log="")
        try:
            r = subprocess.run(
                [sys.executable, str(STUDIO / "educator.py"), question],
                capture_output=True, text=True, cwd=str(ROOT), timeout=600)
            log = (r.stdout or "") + (r.stderr or "")
            ok = r.returncode == 0
            _LAST.update(status="ready" if ok else "failed", log=log[-4000:])
            return ok, log
        except Exception as e:  # noqa: BLE001
            _LAST.update(status="failed", log=str(e))
            return False, str(e)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/status"):
            return self._json(200, _LAST)
        return super().do_GET()

    def do_POST(self):
        if not self.path.startswith("/api/ask"):
            return self._json(404, {"error": "unknown endpoint"})
        n = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"error": "invalid JSON"})
        q = (payload.get("question") or "").strip()
        if not q:
            return self._json(400, {"error": "question required"})
        ok, log = _run_educator(q)
        tail = "\n".join(log.splitlines()[-6:])
        return self._json(200 if ok else 422,
                          {"ok": ok, "question": q, "status": _LAST["status"], "log": tail})

    def log_message(self, fmt, *args):
        pass


Handler.extensions_map.update({
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".json": "application/json",
})

socketserver.ThreadingTCPServer.allow_reuse_address = True

if __name__ == "__main__":
    with socketserver.ThreadingTCPServer(("", PORT), Handler) as httpd:
        url = f"http://localhost:{PORT}/studio/viewer/studio.html"
        print(f"[studio-viewer] serving {ROOT}")
        print(f"[studio-viewer] open {url}")
        httpd.serve_forever()
