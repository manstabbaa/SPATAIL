"""worker_manager.py — batch manager for the SPATAIL AI Asset Factory (bpy-free).

Scans assets_raw/ for asset groups, builds a processing queue, and runs each group
through its OWN headless Blender process (process_one_asset.py) — never one long
shared Blender session — so a crash, hang, or bad import isolates to a single asset.
Up to --max-workers run concurrently; each has a hard per-asset timeout. Writes
assets_processed/batch_report.json, a manager log, and a clear console summary.

    python asset_factory/worker_manager.py --input assets_raw --output assets_processed
    python asset_factory/worker_manager.py --input assets_raw --output assets_processed --max-workers 8
    python asset_factory/worker_manager.py --input assets_raw --asset-timeout-seconds 900

This module deliberately imports NO bpy: it only orchestrates subprocesses.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import config  # noqa: E402
import reports  # noqa: E402
import validation  # noqa: E402

for _s in (sys.stdout, sys.stderr):           # tolerate non-ASCII paths on Windows
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

WORKER_SCRIPT = HERE / "process_one_asset.py"
log = logging.getLogger("asset_factory.manager")


def _setup_logging() -> None:
    config.ensure_dirs()
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(config.LOGS_DIR / "worker_manager.log", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    log.addHandler(sh)


def discover_groups(input_dir: Path, only: set | None) -> list[str]:
    """Each immediate subfolder of input_dir holding >=1 importable file is a group."""
    if not input_dir.exists():
        return []
    groups = []
    for child in sorted(input_dir.iterdir()):
        if not child.is_dir():
            continue
        if only and child.name not in only:
            continue
        if any(p.suffix.lower() in config.SUPPORTED_EXTS for p in child.rglob("*") if p.is_file()):
            groups.append(child.name)
        else:
            log.warning("skip %s: no importable source files", child.name)
    return groups


def _run_one(asset_id: str, input_dir: Path, output_dir: Path, blender_exe: str,
             timeout_s: int, use_scratch: bool, status: dict, lock: threading.Lock,
             export_usdz: bool = False) -> dict:
    """Spawn one Blender worker for one asset; return a normalized result dict."""
    with lock:
        status[asset_id] = "processing"
        log.info("[processing] %s", asset_id)

    out_dir = output_dir / asset_id
    out_dir.mkdir(parents=True, exist_ok=True)
    result_sidecar = out_dir / f"{asset_id}.build_result.json"
    try:
        result_sidecar.unlink()                # clear any stale sidecar
    except OSError:
        pass

    cmd = [blender_exe, "--background", "--python", str(WORKER_SCRIPT), "--",
           "--asset-id", asset_id, "--input", str(input_dir), "--output", str(output_dir),
           "--result", str(result_sidecar)]
    if not use_scratch:
        cmd.append("--no-scratch")
    if export_usdz:
        cmd.append("--export-usdz")

    t0 = time.perf_counter()
    crashed_reason = ""
    stderr_tail = ""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        stderr_tail = (proc.stderr or proc.stdout or "")[-1500:]
    except subprocess.TimeoutExpired:
        crashed_reason = f"timeout after {timeout_s}s"
    except Exception as e:  # noqa: BLE001
        crashed_reason = f"failed to launch Blender: {e}"
    elapsed = time.perf_counter() - t0

    result = _read_result(asset_id, out_dir, result_sidecar, crashed_reason,
                          stderr_tail, elapsed, input_dir)

    # Final gate: a "success" must still pass the file/bounds/origin validation.
    if result["status"] == "success":
        cfg = config.load_asset_config(input_dir / asset_id)
        val = validation.validate_asset_output(out_dir, asset_id, cfg)
        result["validation_ok"] = val["ok"]
        if not val["ok"]:
            result["status"] = "failed"
            result["errors"] = (result.get("errors") or []) + val["errors"]

    with lock:
        status[asset_id] = result["status"]
        mark = "ok" if result["status"] == "success" else "FAILED"
        log.info("[%s] %s (%.1fs)%s", mark, asset_id, result.get("total_seconds", elapsed),
                 "" if result["status"] == "success" else f" — {(result.get('errors') or [''])[0]}")
    return result


def _read_result(asset_id, out_dir, sidecar, crashed_reason, stderr_tail, elapsed, input_dir) -> dict:
    """Turn the worker's sidecar (or its absence) into a uniform result dict."""
    import json
    base = {"asset_id": asset_id, "output_dir": str(out_dir), "total_seconds": elapsed,
            "warnings": [], "errors": []}
    if crashed_reason:
        base.update(status="failed",
                    errors=[crashed_reason, f"stderr: {stderr_tail}" if stderr_tail else ""])
        base["errors"] = [e for e in base["errors"] if e]
        return base
    if sidecar.exists():
        try:
            r = json.loads(sidecar.read_text(encoding="utf-8"))
            return {
                "asset_id": asset_id,
                "status": "success" if r.get("ok") else "failed",
                "output_dir": r.get("output_dir", str(out_dir)),
                "glb": r.get("glb", ""),
                "final_bounds": r.get("final_bounds", {}),
                "triangle_count": r.get("triangle_count", 0),
                "total_seconds": r.get("total_seconds", elapsed),
                "warnings": r.get("warnings", []),
                "errors": r.get("errors", []) or ([r.get("error")] if r.get("error") else []),
            }
        except (ValueError, OSError) as e:
            base.update(status="failed", errors=[f"unreadable result sidecar: {e}"])
            return base
    # No sidecar and no captured timeout/launch error → the worker crashed silently.
    base.update(status="failed",
                errors=["Blender worker wrote no result (crash)",
                        f"stderr: {stderr_tail}" if stderr_tail else ""])
    base["errors"] = [e for e in base["errors"] if e]
    return base


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SPATAIL AI Asset Factory — batch manager")
    ap.add_argument("--input", default="assets_raw")
    ap.add_argument("--output", default="assets_processed")
    ap.add_argument("--max-workers", type=int, default=None)
    ap.add_argument("--asset-timeout-seconds", type=int, default=None)
    ap.add_argument("--blender", default=None, help="path to blender.exe (else $BLENDER_EXE)")
    ap.add_argument("--only", nargs="*", default=None, help="restrict to these asset ids")
    ap.add_argument("--no-scratch", action="store_true", help="import source in place")
    ap.add_argument("--export-usdz", action="store_true",
                    help="also emit <id>.normalized.usdz (iOS / RealityKit)")
    args = ap.parse_args(argv)

    _setup_logging()
    config.ensure_dirs()

    input_dir = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    worker_cfg = config.load_worker_config()
    max_workers = config.resolve_max_workers(args.max_workers, worker_cfg)
    timeout_s = (args.asset_timeout_seconds
                 or int(worker_cfg.get("asset_timeout_seconds", 600)))
    blender_exe = config.resolve_blender_exe(args.blender)
    use_scratch = (not args.no_scratch) and bool(worker_cfg.get("copy_assets_to_local_scratch", True))

    log.info("SPATAIL Asset Factory — input=%s output=%s", input_dir, output_dir)
    log.info("max_workers=%d (RAM=%.0fGB) asset_timeout=%ds blender=%s",
             max_workers, config.total_ram_gb(), timeout_s, blender_exe)

    if not os.path.exists(blender_exe):
        log.error("Blender not found at %r — set --blender or $BLENDER_EXE", blender_exe)
        return 2

    groups = discover_groups(input_dir, set(args.only) if args.only else None)
    if not groups:
        log.error("no asset groups with importable files under %s", input_dir)
        # Still emit an (empty) batch report so downstream tooling has a file.
        batch = reports.build_batch_report(input_dir=str(input_dir), output_dir=str(output_dir),
                                           max_workers=max_workers, total_seconds=0.0, results=[])
        reports.write_json(output_dir / "batch_report.json", batch)
        return 1

    log.info("queued %d asset group(s): %s", len(groups), ", ".join(groups))
    status = {g: "pending" for g in groups}
    lock = threading.Lock()
    results: list = []
    t_batch = time.perf_counter()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_run_one, g, input_dir, output_dir, blender_exe,
                               timeout_s, use_scratch, status, lock, args.export_usdz): g
                   for g in groups}
        for fut in as_completed(futures):
            g = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:  # noqa: BLE001 — a manager-side error still isolates
                log.exception("manager error processing %s", g)
                results.append({"asset_id": g, "status": "failed", "output_dir": str(output_dir / g),
                                "errors": [f"manager error: {e}"], "warnings": [], "total_seconds": 0.0})

    total_seconds = time.perf_counter() - t_batch
    results.sort(key=lambda r: r.get("asset_id", ""))
    batch = reports.build_batch_report(input_dir=str(input_dir), output_dir=str(output_dir),
                                       max_workers=max_workers, total_seconds=total_seconds,
                                       results=results)
    batch_path = reports.write_json(output_dir / "batch_report.json", batch)
    log.info("wrote batch report: %s", batch_path)

    print(reports.console_summary(batch))
    return 0 if batch["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
