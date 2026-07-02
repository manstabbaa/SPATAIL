"""web_viewer.py — LIVE web QA viewer for normalized AI Asset Factory assets.

    python asset_factory/web_viewer.py [--port 8790] [--host 0.0.0.0]

Zero-dependency (stdlib) server + a <model-viewer> gallery that renders the REAL
normalized GLB and AR-Quick-Looks the USDZ on iOS — no Xcode, no app build, and
NO primitives: it loads the actual mesh files the factory produced. Open it in the
phone browser over Tailscale (http://<pc>.<tailnet>.ts.net:8790/).

Routes (all one origin, so no CORS juggling):
    GET /                       the model-viewer gallery page
    GET /index                  JSON list of processed assets (+ urls)
    GET /file/<id>/<name>       the GLB / USDZ / preview.png bytes
"""
from __future__ import annotations

import argparse
import json
import http.server
import socketserver
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import config  # noqa: E402  (bpy-free; gives REPO_ROOT)

PROCESSED = config.REPO_ROOT / "assets_processed"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def build_index() -> dict:
    """List successfully-processed assets from assets_processed/*/asset_manifest.json."""
    assets = []
    if PROCESSED.is_dir():
        for man in sorted(PROCESSED.glob("*/asset_manifest.json")):
            try:
                m = json.loads(man.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if m.get("status") != "success":
                continue
            aid = m.get("asset_id") or man.parent.name
            d = man.parent
            has_usdz = (d / f"{aid}.normalized.usdz").is_file()
            has_glb = (d / f"{aid}.normalized.glb").is_file()
            has_prev = (d / "preview.png").is_file()
            assets.append({
                "id": aid,
                "title": aid.replace("_", " ").title(),
                "bounds_mode": m.get("bounds_mode"),
                "origin_mode": m.get("origin_mode"),
                "target_bounds_m": m.get("target_bounds_m", {}),
                "final_bounds_m": m.get("final_bounds_m", {}),
                "triangle_count": m.get("triangle_count", 0),
                "glb_url": f"/file/{aid}/{aid}.normalized.glb" if has_glb else "",
                "usdz_url": f"/file/{aid}/{aid}.normalized.usdz" if has_usdz else "",
                "preview_url": f"/file/{aid}/preview.png" if has_prev else "",
            })
    return {"assets": assets, "count": len(assets)}


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>SPATAIL Asset Factory — live viewer</title>
<script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/3.5.0/model-viewer.min.js"></script>
<style>
  :root { color-scheme: dark; }
  body { margin:0; background:#0d0f12; color:#e7e9ee; font:15px/1.4 -apple-system,Segoe UI,Roboto,sans-serif; }
  header { padding:16px 18px; border-bottom:1px solid #20242b; position:sticky; top:0; background:#0d0f12ee; backdrop-filter:blur(8px); }
  header h1 { font-size:17px; margin:0; } header p { margin:4px 0 0; color:#9aa1ad; font-size:12px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:14px; padding:14px; }
  .card { background:#15181e; border:1px solid #20242b; border-radius:14px; overflow:hidden; }
  model-viewer { width:100%; height:300px; background:#1b1f27; --poster-color:#1b1f27; }
  .meta { padding:10px 12px; }
  .meta .t { font-weight:600; }
  .meta .s { color:#9aa1ad; font-size:12px; margin-top:3px; }
  .badge { display:inline-block; font-size:11px; padding:2px 7px; border-radius:999px; margin-top:6px; }
  .ok { background:#143d2a; color:#5bd99a; } .warn { background:#3d2e14; color:#e0b35b; }
  .empty { padding:40px; text-align:center; color:#9aa1ad; }
  code { background:#1b1f27; padding:1px 6px; border-radius:6px; }
</style>
</head>
<body>
<header>
  <h1>SPATAIL Asset Factory — live viewer</h1>
  <p>Real normalized GLB rendered in 3D · tap <b>View in AR</b> on iPhone for AR Quick Look (USDZ). No app build.</p>
</header>
<div id="grid" class="grid"></div>
<script>
function fmt(b){ return b ? `${(+b.x).toFixed(2)} × ${(+b.y).toFixed(2)} × ${(+b.z).toFixed(2)} m` : "—"; }
function fits(a){ const t=a.target_bounds_m||{}, f=a.final_bounds_m||{};
  return (+f.x<=+t.x+1e-3)&&(+f.y<=+t.y+1e-3)&&(+f.z<=+t.z+1e-3); }
fetch("/index").then(r=>r.json()).then(d=>{
  const g=document.getElementById("grid");
  if(!d.assets.length){ g.innerHTML='<div class="empty">No processed assets yet.<br>Run <code>python asset_factory/worker_manager.py --input assets_raw --output assets_processed --export-usdz</code></div>'; return; }
  for(const a of d.assets){
    const el=document.createElement("div"); el.className="card";
    const ok=fits(a);
    el.innerHTML=`
      <model-viewer src="${a.glb_url}" ${a.usdz_url?`ios-src="${a.usdz_url}"`:""}
        ${a.preview_url?`poster="${a.preview_url}"`:""}
        camera-controls auto-rotate shadow-intensity="1" exposure="1"
        ar ar-modes="quick-look webxr scene-viewer" ar-scale="fixed"
        alt="${a.title}"></model-viewer>
      <div class="meta">
        <div class="t">${a.title}</div>
        <div class="s">Normalized ${fmt(a.final_bounds_m)} · target ${fmt(a.target_bounds_m)}</div>
        <div class="s">${a.triangle_count} tris · ${a.bounds_mode||"—"} · ${a.origin_mode||"—"}</div>
        <span class="badge ${ok?"ok":"warn"}">${ok?"✓ fits target bounds":"⚠ exceeds target"}</span>
      </div>`;
    g.appendChild(el);
  }
}).catch(e=>{ document.getElementById("grid").innerHTML='<div class="empty">Could not load /index: '+e+'</div>'; });
</script>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/index":
            return self._send(200, json.dumps(build_index()).encode("utf-8"), "application/json")
        if path.startswith("/file/"):
            base = PROCESSED.resolve()
            fp = (base / path[len("/file/"):]).resolve()
            if not str(fp).startswith(str(base)) or not fp.is_file():
                return self._send(404, b'{"error":"not found"}', "application/json")
            ext = fp.suffix.lower()
            ctype = ("model/gltf-binary" if ext == ".glb"
                     else "model/vnd.usdz+zip" if ext == ".usdz"
                     else "image/png" if ext == ".png"
                     else "application/octet-stream")
            return self._send(200, fp.read_bytes(), ctype)
        return self._send(404, b'{"error":"not found"}', "application/json")

    def log_message(self, *a):
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="SPATAIL Asset Factory live web viewer")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8790)
    args = ap.parse_args()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    n = build_index()["count"]
    with socketserver.ThreadingTCPServer((args.host, args.port), Handler) as httpd:
        print(f"[factory-viewer] serving {PROCESSED}  ({n} asset(s))")
        print(f"[factory-viewer] local : http://127.0.0.1:{args.port}/")
        print(f"[factory-viewer] phone : http://<pc>.<tailnet>.ts.net:{args.port}/  (over Tailscale)")
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
