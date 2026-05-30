"""Generative bridge — turn a build plan into a real, registered library asset.

This is the headless-Blender half of the generative manual->XR path. When a
manual matches no curated model, the segment agent produces a per-part build
plan; this bridge spawns Blender (background) to BUILD that plan part-by-part,
export a GLB, and write the registry + animation library — then registers the
result as a first-class library asset.

Mirrors bake_bridge.py exactly: subprocess Blender --background --python
<driver> -- <spec.json>, driver writes a result sidecar, we read it back. No
live Blender session is touched, so the user's open scene is never disturbed.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

try:
    from . import asset_library as lib
except ImportError:
    import asset_library as lib


REPO_ROOT = Path(__file__).resolve().parents[2]                 # C:/SPATAIL_MAX
ENGINE_DIR = REPO_ROOT / "engineexplainer" / "engine"
BUILD_SCRIPT = REPO_ROOT / "pipeline" / "blender" / "spatail_build_from_plan_driver.py"
BLENDER_EXE = os.environ.get(
    "ENGINEEXPLAINER_BLENDER_EXE",
    r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
)

# Real-CAD stage (build123d / OpenCascade via the vendored text-to-cad skill).
# Runs in its own Python venv (>=3.11) BEFORE Blender to produce per-part CAD
# meshes; the driver imports those instead of primitives. Best-effort: if the
# venv or toolchain is missing the build still succeeds with primitive parts.
CAD_BUILD_SCRIPT = REPO_ROOT / "pipeline" / "cad" / "spatail_cad_build.py"
CAD_PYTHON = os.environ.get(
    "ENGINEEXPLAINER_CAD_PYTHON",
    str(REPO_ROOT / "vendor" / "text-to-cad" / ".venv" / "Scripts" / "python.exe"),
)
USE_CAD = os.environ.get("ENGINEEXPLAINER_USE_CAD", "1") not in ("0", "false", "no", "")

# Live-Blender mirror: after the headless build exports the GLB, push it into the
# user's always-open Blender session (the lab_blender_org MCP add-on on
# localhost:9876) so they SEE the assembled, correctly-scaled asset appear.
# Best-effort and fully decoupled: a closed Blender just skips — the headless
# build + web mini-app never depend on it. Toggle with ENGINEEXPLAINER_LIVE_BLENDER.
LIVE_BLENDER = os.environ.get("ENGINEEXPLAINER_LIVE_BLENDER", "1") not in ("0", "false", "no", "")
LIVE_BLENDER_SCRIPT = REPO_ROOT / "pipeline" / "blender" / "spatail_live_blender.py"


def asset_paths(asset_id: str) -> dict:
    return {
        "glb_path":      str(ENGINE_DIR / f"{asset_id}.glb"),
        "registry_path": str(ENGINE_DIR / f"{asset_id}_part_registry.json"),
        "anim_path":     str(ENGINE_DIR / f"{asset_id}_animation_library.json"),
    }


def generate_cad_manifest(plan: dict, *, asset_id: str, timeout: int = 600) -> str | None:
    """Run the CAD generation stage to model the plan's parts as real
    build123d/OpenCascade geometry. Returns the manifest path, or None if the
    stage is disabled, unavailable, or fails (so the build degrades to
    primitives rather than erroring)."""
    if not USE_CAD:
        return None
    if not Path(CAD_PYTHON).exists() or not CAD_BUILD_SCRIPT.exists():
        print(f"[generative_bridge] CAD stage unavailable "
              f"(python={CAD_PYTHON}, script={CAD_BUILD_SCRIPT}); using primitives.")
        return None

    out_dir = ENGINE_DIR / "cad_parts" / asset_id
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "cad_manifest.json"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".plan.json", delete=False,
                                     encoding="utf-8") as f:
        plan_path = f.name
        json.dump(plan, f)

    cmd = [CAD_PYTHON, str(CAD_BUILD_SCRIPT), plan_path, str(out_dir),
           "--manifest", str(manifest_path), "--cad-all"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        print(f"[generative_bridge] CAD stage crashed: {e}; using primitives.")
        return None
    finally:
        try:
            os.unlink(plan_path)
        except OSError:
            pass

    if not manifest_path.exists():
        print("[generative_bridge] CAD stage wrote no manifest; using primitives. "
              f"stderr tail:\n{proc.stderr[-800:]}")
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        n_ok = manifest.get("n_ok", 0)
    except Exception:
        n_ok = 0
    if not n_ok:
        print("[generative_bridge] CAD stage produced 0 parts; using primitives.")
        return None
    print(f"[generative_bridge] CAD stage built {n_ok} real-CAD part(s) -> {manifest_path}")
    return str(manifest_path)


def _mirror_to_live(glb_path: str, asset_id: str) -> dict:
    """Best-effort: push the built GLB into the user's live Blender session.

    Imports the stdlib-only socket client lazily by path so a missing file or a
    closed Blender can never break the headless build. Returns a status dict
    ({ok, skipped, reason?, ...}) and logs a one-line summary.
    """
    if not LIVE_BLENDER:
        return {"ok": False, "skipped": True,
                "reason": "disabled (ENGINEEXPLAINER_LIVE_BLENDER=0)"}
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "spatail_live_blender", str(LIVE_BLENDER_SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        status = mod.mirror_asset_to_live(glb_path, asset_id)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "skipped": True,
                "reason": f"live-mirror import/call failed: {e}"}

    if status.get("ok"):
        print(f"[generative_bridge] mirrored '{asset_id}' into LIVE Blender "
              f"({status.get('n_meshes')} parts, extents {status.get('extents_m')} m).")
    elif status.get("skipped"):
        print(f"[generative_bridge] live Blender mirror skipped: {status.get('reason')}")
    else:
        print(f"[generative_bridge] live Blender mirror error: {status.get('reason')}")
    return status


def build_asset_from_plan(plan: dict, *, asset_id: str | None = None,
                          timeout: int = 600) -> dict:
    """Spawn Blender headlessly to build the plan into a GLB + registry.

    Returns the driver's result dict: {ok, assetId, glb_path, registry_path,
    anim_path, n_parts, bbox_m, camera_presets, assembly_offsets}.
    Raises on any failure (Blender missing, driver crash, no sidecar).
    """
    asset_id = asset_id or plan.get("assetId") or "generated_asset"
    plan = dict(plan)
    plan["assetId"] = asset_id

    if not Path(BLENDER_EXE).exists():
        raise FileNotFoundError(
            f"Blender not found at {BLENDER_EXE}. Set ENGINEEXPLAINER_BLENDER_EXE.")
    if not BUILD_SCRIPT.exists():
        raise FileNotFoundError(f"Build driver missing at {BUILD_SCRIPT}")

    paths = asset_paths(asset_id)
    result_path = str(Path(tempfile.gettempdir()) / f"{asset_id}.build_result.json")
    # Clear any stale sidecar so we never read a previous run's result.
    try:
        os.unlink(result_path)
    except FileNotFoundError:
        pass

    # Model the parts as real CAD geometry first (best-effort; None → primitives).
    cad_manifest = generate_cad_manifest(plan, asset_id=asset_id, timeout=timeout)

    spec = {"assetId": asset_id, "plan": plan, "result_path": result_path,
            "cad_manifest": cad_manifest, **paths}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        spec_path = f.name
        json.dump(spec, f)

    cmd = [BLENDER_EXE, "--background", "--python", str(BUILD_SCRIPT), "--", spec_path]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    try:
        os.unlink(spec_path)
    except OSError:
        pass

    if not Path(result_path).exists():
        raise RuntimeError(
            "build driver did not write a result sidecar. Blender stderr tail:\n"
            f"{proc.stderr[-1800:]}")
    result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"build failed: {result.get('error')}\n"
                           f"{result.get('trace', '')[:1800]}")
    result["elapsed_s"] = round(time.time() - t0, 1)

    # Mirror the freshly-exported GLB into the user's live Blender session so the
    # assembled, correctly-scaled asset appears there (best-effort; never fatal).
    result["live_mirror"] = _mirror_to_live(paths["glb_path"], asset_id)
    return result


def build_and_register(segment: dict, *, timeout: int = 600) -> dict:
    """Full generative step: build the segment's plan, then register the result
    as a library asset (in memory + persisted manifest). Returns
    {result, asset, registry_path}.
    """
    try:
        from .manual_segment import build_plan_from_segment
    except ImportError:
        from manual_segment import build_plan_from_segment

    asset_id = segment["assetId"]
    plan = build_plan_from_segment(segment)
    result = build_asset_from_plan(plan, asset_id=asset_id, timeout=timeout)

    keywords = list(dict.fromkeys(
        [w.lower() for w in segment.get("product_keywords", [])]
        + [segment.get("product_kind", "").lower()]
        + [segment.get("kind", "").lower()]
    ))
    keywords = [k for k in keywords if k]

    asset = lib.register_generated_asset(
        asset_id=asset_id,
        kind=segment.get("kind", asset_id),
        keywords=keywords,
        glb=f"../engine/{asset_id}.glb",
        registry=f"{asset_id}_part_registry.json",
        animation_library=f"{asset_id}_animation_library.json",
        notes=f"Generated from a manual ({result['n_parts']} parts, "
              f"{result.get('n_cad', 0)} modelled as real CAD, "
              f"built in {result.get('elapsed_s', '?')}s).",
    )
    return {"result": result, "asset": asset,
            "registry_path": result["registry_path"]}


if __name__ == "__main__":
    import sys
    try:
        from .manual_segment import segment_manual
    except ImportError:
        from manual_segment import segment_manual
    txt = (Path(sys.argv[1]).read_text(encoding="utf-8")
           if len(sys.argv) > 1 and Path(sys.argv[1]).exists()
           else "IKEA KALLAX 1x4 shelf assembly")
    seg = segment_manual(txt)
    out = build_and_register(seg)
    print(json.dumps({"asset_id": out["asset"].asset_id,
                      "n_parts": out["result"]["n_parts"],
                      "glb": out["asset"].glb,
                      "elapsed_s": out["result"].get("elapsed_s")}, indent=2))
