"""meshy_client.py — Meshy multi-image-to-3D: submit views -> poll -> download.

    python studio/meshy/meshy_client.py simple_pendulum   # uses studio/out/meshy/<id>/view_*.png

Sends the local Gemini PNGs as base64 data URIs (no hosting needed), polls the
task to completion, and downloads GLB + USDZ into studio/out/meshy/<id>/meshy/.
Reads MESHY_API_KEY from ~/.spatail/secrets.env via config (never committed).
Each completed generation consumes ~30 Meshy credits (logged from the response).
"""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT_ROOT = REPO / "studio" / "out" / "meshy"
BASE = "https://api.meshy.ai/openapi/v1/multi-image-to-3d"
BALANCE = "https://api.meshy.ai/openapi/v1/balance"


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.require('MESHY_API_KEY')}",
            "Content-Type": "application/json"}


def _data_uri(p: Path) -> str:
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def balance() -> dict | None:
    """Best-effort account balance (also validates the key)."""
    try:
        req = urllib.request.Request(BALANCE, headers=_headers())
        return json.load(urllib.request.urlopen(req, timeout=30))
    except Exception as e:  # noqa: BLE001
        print(f"[meshy] balance check: {e!r}")
        return None


def submit(image_paths, *, ai_model="meshy-6", should_texture=True, enable_pbr=True,
           target_polycount=30000, topology="triangle",
           target_formats=("glb", "usdz"), image_enhancement=True) -> str:
    imgs = [_data_uri(Path(p)) for p in image_paths][:4]
    if not imgs:
        raise ValueError("no images to submit")
    body = {
        "image_urls": imgs, "ai_model": ai_model, "should_texture": should_texture,
        "enable_pbr": enable_pbr, "target_polycount": target_polycount,
        "topology": topology, "target_formats": list(target_formats),
        "image_enhancement": image_enhancement,
    }
    data = json.dumps(body).encode()
    for fmts in (list(target_formats), ["glb"]):       # retry without usdz if rejected
        body["target_formats"] = fmts
        data = json.dumps(body).encode()
        try:
            req = urllib.request.Request(BASE, data=data, headers=_headers(), method="POST")
            obj = json.load(urllib.request.urlopen(req, timeout=90))
            return obj.get("result") or obj.get("id")
        except urllib.error.HTTPError as e:
            msg = e.read()[:300].decode("utf-8", "ignore")
            if e.code == 400 and "format" in msg.lower() and fmts != ["glb"]:
                print(f"[meshy] format {fmts} rejected, retrying glb-only")
                continue
            raise RuntimeError(f"submit HTTP {e.code}: {msg}")
    raise RuntimeError("submit failed")


def poll(task_id: str, *, timeout_s: int = 900, interval: int = 6) -> dict:
    url = f"{BASE}/{task_id}"
    t0 = time.time()
    last = -1
    while True:
        req = urllib.request.Request(url, headers=_headers())
        obj = json.load(urllib.request.urlopen(req, timeout=60))
        st, pr = obj.get("status"), obj.get("progress", 0)
        if pr != last:
            print(f"[meshy] {task_id[:8]} {st} {pr}%"); last = pr
        if st in ("SUCCEEDED", "FAILED", "CANCELED"):
            return obj
        if time.time() - t0 > timeout_s:
            raise TimeoutError(f"task {task_id} still {st} after {timeout_s}s")
        time.sleep(interval)


def _download(url: str, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url)
    raw = urllib.request.urlopen(req, timeout=180).read()
    dest.write_bytes(raw)
    return len(raw)


def generate_3d(image_paths, name: str, out_root: Path = OUT_ROOT, **kw) -> dict:
    out = out_root / name / "meshy"
    task_id = submit(image_paths, **kw)
    print(f"[meshy] submitted {name} -> task {task_id}")
    res = poll(task_id)
    info = {"name": name, "task_id": task_id, "status": res.get("status"),
            "consumed_credits": res.get("consumed_credits"), "files": {}}
    if res.get("status") != "SUCCEEDED":
        info["error"] = res.get("task_error") or res
        return info
    urls = res.get("model_urls", {}) or {}
    for fmt in ("glb", "usdz", "obj", "fbx"):
        u = urls.get(fmt)
        if u:
            n = _download(u, out / f"{name}.{fmt}")
            info["files"][fmt] = {"path": str(out / f"{name}.{fmt}"), "bytes": n}
    if res.get("thumbnail_url"):
        _download(res["thumbnail_url"], out / "thumbnail.png")
        info["files"]["thumbnail"] = str(out / "thumbnail.png")
    (out / "result.json").write_text(json.dumps({**info, "raw": res}, indent=2), encoding="utf-8")
    print(f"[meshy] {name}: {res.get('status')} | {res.get('consumed_credits')} credits | "
          f"{sorted(info['files'])}")
    return info


def main() -> int:
    ids = sys.argv[1:]
    if not ids:
        print("usage: meshy_client.py <assetId> [assetId...]"); return 2
    b = balance()
    if b is not None:
        print(f"[meshy] balance: {b}")
    summary = []
    for aid in ids:
        views = sorted((OUT_ROOT / aid).glob("view_*.png"))
        if not views:
            print(f"[meshy] no views for {aid} (run gemini_images.py first)"); continue
        summary.append(generate_3d([str(v) for v in views], aid))
    total = sum((s.get("consumed_credits") or 0) for s in summary)
    print(f"[meshy] done: {len(summary)} assets, ~{total} credits consumed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
