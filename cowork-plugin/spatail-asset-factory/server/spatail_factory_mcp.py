"""spatail_factory_mcp.py — LOCAL stdio MCP server for the SPATAIL AI Asset Factory.

Lets Cowork run the user's LOCAL Blender on their PC: each tool shells out to the
factory's existing Python entry points (which spawn headless Blender via subprocess),
so the server stays a thin, DRY wrapper over proven code — it does NOT import bpy or
the factory modules.

Launched locally by the Cowork plugin's .mcp.json (stdio transport). Locates the repo
via $SPATAIL_REPO (default C:\\SPATAIL_MAX) and Blender via $BLENDER_EXE.

Tools: factory_status, create_asset, list_assets, get_asset, publish_asset,
start_viewer, stop_viewer.

Requires the `mcp` package (pip install mcp). Never writes to stdout except via the
MCP protocol (stdio uses stdout) — diagnostics go to stderr.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP

REPO = Path(os.environ.get("SPATAIL_REPO", r"C:\SPATAIL_MAX"))
BLENDER = os.environ.get("BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")
AF = REPO / "asset_factory"
ASSETS_RAW = REPO / "assets_raw"
ASSETS_PROCESSED = REPO / "assets_processed"
DEFAULT_VIEWER_PORT = 8790
SUPPORTED_EXTS = (".glb", ".gltf", ".fbx", ".obj", ".stl")

mcp = FastMCP("spatail-asset-factory")


# ── helpers (testable; the @mcp.tool wrappers below just call these) ──────────
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "asset"


def _py() -> str:
    return sys.executable or "python"


def _env() -> dict:
    return {**os.environ, "BLENDER_EXE": BLENDER, "SPATAIL_REPO": str(REPO)}


def _run(cmd: list[str], timeout: int) -> tuple[int | None, str]:
    """Run a child process DETACHED from this server's MCP stdio transport.

    Critical for an stdio MCP server: the child (worker_manager) itself spawns Blender,
    so we must NOT let it inherit our stdin/stdout (the JSON-RPC pipe) — that nests
    pipes and deadlocks. So: stdin=DEVNULL and stdout/stderr → a temp file (never a
    PIPE). Returns (returncode|None-on-timeout, captured_output_text).
    """
    fd, path = tempfile.mkstemp(suffix=".aflog")
    try:
        with open(fd, "wb") as f:
            try:
                proc = subprocess.run(cmd, cwd=str(REPO), env=_env(),
                                      stdin=subprocess.DEVNULL, stdout=f,
                                      stderr=subprocess.STDOUT, timeout=timeout)
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                rc = None
        return rc, Path(path).read_text(encoding="utf-8", errors="replace")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _factory_status() -> dict:
    wm = AF / "worker_manager.py"
    n = len(list(ASSETS_PROCESSED.glob("*/asset_manifest.json"))) if ASSETS_PROCESSED.is_dir() else 0
    return {
        "repo": str(REPO), "repo_found": wm.is_file(),
        "blender": BLENDER, "blender_found": os.path.exists(BLENDER),
        "assets_raw": str(ASSETS_RAW), "assets_processed": str(ASSETS_PROCESSED),
        "processed_count": n,
        "ready": wm.is_file() and os.path.exists(BLENDER),
    }


def _read_manifest(asset_id: str) -> dict | None:
    mf = ASSETS_PROCESSED / asset_id / "asset_manifest.json"
    if not mf.is_file():
        return None
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _asset_summary(asset_id: str, m: dict) -> dict:
    d = ASSETS_PROCESSED / asset_id
    fb = m.get("final_bounds_m", {})
    tb = m.get("target_bounds_m", {})
    fits = all(fb.get(k, 0) <= tb.get(k, 0) + 1e-3 for k in ("x", "y", "z")) if fb and tb else None
    ex = m.get("exported_files", {}) or {}
    return {
        "asset_id": asset_id, "status": m.get("status"),
        "final_bounds_m": fb, "target_bounds_m": tb, "fits_target": fits,
        "bounds_mode": m.get("bounds_mode"), "origin_mode": m.get("origin_mode"),
        "triangle_count": m.get("triangle_count"), "vertex_count": m.get("vertex_count"),
        "has_usdz": bool(ex.get("usdz")), "has_preview": bool(ex.get("preview")),
        "output_dir": str(d),
        "glb_path": str(d / f"{asset_id}.normalized.glb"),
        "usdz_path": str(d / f"{asset_id}.normalized.usdz") if ex.get("usdz") else "",
        "preview_path": str(d / "preview.png") if ex.get("preview") else "",
    }


def _seed_demo(asset_id: str) -> Path:
    """Copy a recognizable library model into assets_raw/<id>/ for a quick demo."""
    candidates = [
        REPO / "public/assets/spatail-library/mechanical/gear_large.glb",
        REPO / "public/assets/spatail-library/astronomy/earth.glb",
        REPO / "public/assets/spatail-library/architecture/colosseum.glb",
        REPO / "public/assets/spatail-library/mechanical/crankshaft.glb",
    ]
    src = next((c for c in candidates if c.is_file()), None)
    if not src:
        raise RuntimeError("no library model available to seed a demo from")
    dst = ASSETS_RAW / asset_id
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst / "model.glb")
    return dst


def _stage_source(source: str, asset_id: str) -> str:
    """Resolve `source` into an assets_raw group; return the resolved asset_id.

    source may be: a path to a model file, a path to a folder of model files, or the
    name of an existing assets_raw group.
    """
    p = Path(source)
    # existing group by id
    if (ASSETS_RAW / source).is_dir() and any(
            f.suffix.lower() in SUPPORTED_EXTS for f in (ASSETS_RAW / source).rglob("*") if f.is_file()):
        return source
    if not p.exists():
        raise RuntimeError(f"source not found: {source!r} (give a model file/folder path "
                           f"or an existing assets_raw group id)")
    aid = asset_id or _slug(p.stem if p.is_file() else p.name)
    dst = ASSETS_RAW / aid
    dst.mkdir(parents=True, exist_ok=True)
    if p.is_file():
        if p.suffix.lower() not in SUPPORTED_EXTS:
            raise RuntimeError(f"unsupported model type {p.suffix!r}; supported: {', '.join(SUPPORTED_EXTS)}")
        shutil.copy(p, dst / p.name)
        # bring along a sibling textures/ folder or .mtl if present (OBJ)
        for extra in ("textures",):
            if (p.parent / extra).is_dir():
                shutil.copytree(p.parent / extra, dst / extra, dirs_exist_ok=True)
        mtl = p.with_suffix(".mtl")
        if mtl.is_file():
            shutil.copy(mtl, dst / mtl.name)
    else:  # folder
        shutil.copytree(p, dst, dirs_exist_ok=True)
    return aid


def _create_asset(source: str, asset_id: str, export_usdz: bool, demo: bool,
                  publish: bool, subject: str, timeout_seconds: int) -> dict:
    status = _factory_status()
    if not status["repo_found"]:
        return {"ok": False, "error": f"asset_factory not found under {REPO} "
                f"(set SPATAIL_REPO to your SPATAIL repo)."}
    if not status["blender_found"]:
        return {"ok": False, "error": f"Blender not found at {BLENDER} (set BLENDER_EXE)."}

    # resolve the input → an assets_raw group
    if demo and not source:
        aid = asset_id or "demo_asset"
        _seed_demo(aid)
    elif source:
        aid = _stage_source(source, asset_id)
    else:
        return {"ok": False, "error": "provide `source` (a model file/folder or an existing "
                "assets_raw group id), or set demo=true to seed a sample model."}

    cmd = [_py(), str(AF / "worker_manager.py"),
           "--input", str(ASSETS_RAW), "--output", str(ASSETS_PROCESSED), "--only", aid]
    if export_usdz:
        cmd.append("--export-usdz")
    rc, log = _run(cmd, timeout_seconds)

    m = _read_manifest(aid)
    if not m:
        why = (f"factory timed out after {timeout_seconds}s" if rc is None
               else "no manifest produced (factory failed)")
        return {"ok": False, "asset_id": aid, "error": why, "log_tail": log[-1200:]}
    summary = _asset_summary(aid, m)
    summary["ok"] = m.get("status") == "success"
    if not summary["ok"]:
        summary["errors"] = m.get("errors", [])

    if publish and summary["ok"]:
        summary["publish"] = _publish_asset(aid, subject or aid.replace("_", " "))
    return summary


def _list_assets() -> dict:
    out = []
    if ASSETS_PROCESSED.is_dir():
        for man in sorted(ASSETS_PROCESSED.glob("*/asset_manifest.json")):
            m = _read_manifest(man.parent.name)
            if m:
                out.append(_asset_summary(man.parent.name, m))
    return {"assets": out, "count": len(out)}


def _get_asset(asset_id: str) -> dict:
    m = _read_manifest(asset_id)
    if not m:
        return {"ok": False, "error": f"no processed asset '{asset_id}'"}
    s = _asset_summary(asset_id, m)
    s["ok"] = True
    s["source_files"] = m.get("source_files", [])
    s["warnings"] = m.get("warnings", [])
    s["timings_seconds"] = m.get("timings_seconds", {})
    return s


def _publish_asset(asset_id: str, subject: str) -> dict:
    cmd = [_py(), str(AF / "publish_to_spatail.py"), "--asset-id", asset_id,
           "--subject", subject or asset_id.replace("_", " ")]
    rc, out = _run(cmd, 600)
    ok = rc == 0 and "verified   : OK" in out
    return {"ok": ok, "asset_id": asset_id, "subject": subject, "output_tail": out[-800:]}


def _viewer_up(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/index", timeout=2):
            return True
    except Exception:
        return False


def _pidfile(port: int) -> Path:
    return AF / "cache" / f"viewer_{port}.pid"


def _start_viewer(port: int) -> dict:
    if _viewer_up(port):
        return {"ok": True, "already_running": True, **_viewer_urls(port)}
    cmd = [_py(), str(AF / "web_viewer.py"), "--host", "0.0.0.0", "--port", str(port)]
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    proc = subprocess.Popen(cmd, cwd=str(REPO), env=_env(),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags)
    pf = _pidfile(port)
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(proc.pid), encoding="utf-8")
    return {"ok": True, "started": True, "pid": proc.pid, **_viewer_urls(port)}


def _viewer_urls(port: int) -> dict:
    return {"port": port, "local_url": f"http://127.0.0.1:{port}/",
            "phone_url_hint": f"http://<your-pc>.<tailnet>.ts.net:{port}/  (over Tailscale)"}


def _stop_viewer(port: int) -> dict:
    pf = _pidfile(port)
    if not pf.is_file():
        return {"ok": False, "error": f"no tracked viewer on port {port}"}
    try:
        pid = int(pf.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return {"ok": False, "error": "unreadable pidfile"}
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        else:
            os.kill(pid, 15)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"could not stop pid {pid}: {e}"}
    pf.unlink(missing_ok=True)
    return {"ok": True, "stopped_pid": pid, "port": port}


# ── MCP tools (thin wrappers; docstrings are the tool descriptions) ───────────
@mcp.tool()
def factory_status() -> dict:
    """Check that the SPATAIL repo and local Blender are reachable before creating
    assets. Returns the resolved repo path, Blender path, whether each was found, and
    how many assets are already processed."""
    return _factory_status()


@mcp.tool()
def create_asset(source: str = "", asset_id: str = "", export_usdz: bool = True,
                 demo: bool = False, publish: bool = False, subject: str = "",
                 timeout_seconds: int = 900) -> dict:
    """Create a normalized, XR-ready SPATAIL asset by running the AI Asset Factory on
    the user's LOCAL Blender. Ingests a raw AI-generated 3D model, cleans it, normalizes
    it to fixed bounds (default 1x1x1 m, bottom-center), and exports a normalized GLB +
    (optionally) USDZ + preview + report.

    source: path to a raw model file (GLB/GLTF/OBJ/FBX/STL) or a folder of model files,
            OR the name of an existing assets_raw group. Leave empty with demo=true to
            seed a recognizable sample model.
    asset_id: optional name/slug for the asset (derived from source if omitted).
    export_usdz: also emit a RealityKit USDZ (for iOS AR). Default true.
    demo: seed a sample model when no source is given.
    publish: also publish the asset into the SPATAIL app library (needs subject).
    subject: short phrase to publish under (e.g. "a gear").
    Returns the asset summary (status, final bounds, fits_target, triangle_count,
    file paths incl. preview_path). This RUNS LOCAL BLENDER and may take ~10-60s."""
    return _create_asset(source, asset_id, export_usdz, demo, publish, subject, timeout_seconds)


@mcp.tool()
def list_assets() -> dict:
    """List all assets the factory has produced (from assets_processed/), each with its
    status, normalized bounds, fits_target, triangle count, and file paths."""
    return _list_assets()


@mcp.tool()
def get_asset(asset_id: str) -> dict:
    """Get one processed asset's details: status, bounds, counts, warnings, timings, and
    the on-disk GLB/USDZ/preview paths."""
    return _get_asset(asset_id)


@mcp.tool()
def publish_asset(asset_id: str, subject: str) -> dict:
    """Publish a processed asset into the SPATAIL app library under a subject so the iOS
    app's Representation flow resolves it. subject is a short phrase (e.g. "a gear")."""
    return _publish_asset(asset_id, subject)


@mcp.tool()
def start_viewer(port: int = DEFAULT_VIEWER_PORT) -> dict:
    """Start the live web viewer (3D in-browser + iOS AR Quick Look) serving every
    processed asset. Returns the local URL and a Tailscale URL hint for the phone.
    Idempotent: reports the existing viewer if one is already running."""
    return _start_viewer(port)


@mcp.tool()
def stop_viewer(port: int = DEFAULT_VIEWER_PORT) -> dict:
    """Stop a web viewer this server started on the given port."""
    return _stop_viewer(port)


if __name__ == "__main__":
    mcp.run()
