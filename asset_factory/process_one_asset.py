"""process_one_asset.py — the single-asset Blender worker (runs INSIDE Blender).

    blender --background --python asset_factory/process_one_asset.py -- \
        --asset-id v8_engine --input assets_raw --output assets_processed

Runs the full ingest pipeline for ONE asset group in an isolated Blender process:
import → inspect → conservative cleanup → fixed-bounds normalization → origin
normalization → (optional collision proxy / LODs) → preview → GLB export →
manifest/report → self-validation. A hard failure (import/normalize/export) is
caught and written as a per-asset failure so the worker manager can keep the batch
going; soft failures (cleanup sub-steps, preview, proxy, LODs) become warnings.

Outputs (assets_processed/<asset_id>/):
    <asset_id>.normalized.glb   asset_manifest.json   asset_report.json
    preview.png                 worker_log.txt        [<asset_id>.build_result.json]
"""
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

# Make the sibling asset_factory modules importable when Blender runs this file
# directly (Blender puts the script dir on sys.path, but be explicit/defensive).
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import bpy  # noqa: E402

import config  # noqa: E402  (bpy-free)
import reports  # noqa: E402  (bpy-free)
import validation  # noqa: E402  (bpy-free)
import importers  # noqa: E402  (bpy)
import inspect_asset  # noqa: E402  (bpy)
import cleanup as cleanup_mod  # noqa: E402  (bpy)
import normalize_bounds  # noqa: E402  (bpy)
import export_asset  # noqa: E402  (bpy)
import preview as preview_mod  # noqa: E402  (bpy)


class _Log:
    """Tee log lines to worker_log.txt and stdout (so the manager captures them)."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(path, "w", encoding="utf-8")

    def __call__(self, msg: str):
        line = f"{msg}"
        print(f"[asset_factory] {line}", flush=True)
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass


def _parse_args(argv):
    args = {"result": None, "scratch": None, "use_scratch": None, "export_usdz": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--asset-id", "--input", "--output", "--result", "--scratch-root"):
            key = a.lstrip("-").replace("-", "_")
            key = {"asset_id": "asset_id", "scratch_root": "scratch"}.get(key, key)
            args[key] = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
        elif a == "--no-scratch":
            args["use_scratch"] = False
            i += 1
        elif a == "--export-usdz":          # force USDZ regardless of config (publish bridge)
            args["export_usdz"] = True
            i += 1
        else:
            i += 1
    return args


def _clear_scene():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for blk in (bpy.data.meshes, bpy.data.materials, bpy.data.images,
                bpy.data.cameras, bpy.data.lights):
        for b in list(blk):
            if b.users == 0:
                try:
                    blk.remove(b)
                except Exception:
                    pass


def _stage_to_scratch(group_src: Path, asset_id: str, worker_cfg: dict, log) -> Path:
    """Copy the group to local scratch if configured (faster IO, source untouched)."""
    root = config.scratch_root(worker_cfg)
    root.mkdir(parents=True, exist_ok=True)
    dst = root / asset_id
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(group_src, dst)
    log(f"staged source to scratch: {dst}")
    return dst


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = _parse_args(argv)
    asset_id = args.get("asset_id")
    input_dir = Path(args.get("input") or "assets_raw")
    output_dir = Path(args.get("output") or "assets_processed")

    if not asset_id:
        print("[asset_factory] FATAL: --asset-id is required", file=sys.stderr)
        return

    group_src = input_dir / asset_id
    out_dir = output_dir / asset_id
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = Path(args["result"]) if args.get("result") else (out_dir / f"{asset_id}.build_result.json")

    log = _Log(out_dir / "worker_log.txt")
    timings: dict = {}
    warnings: list = []
    errors: list = []
    t_total = time.perf_counter()

    # Resolve config up-front so even an early failure reports against real bounds.
    global_cfg = config.load_global_config()
    worker_cfg = config.load_worker_config()
    cfg = config.load_asset_config(group_src, global_cfg)
    for w in cfg.get("_config_warnings", []):
        warnings.append(f"config: {w}")

    source_files = importers.list_all_files(group_src) if group_src.exists() else []
    primary_name = ""

    def _finish_failure(stage: str, exc: Exception):
        tb = traceback.format_exc()
        msg = f"{stage}: {exc}"
        errors.append(msg)
        log(f"FAILED at {stage}: {exc}")
        log(tb)
        timings["total"] = time.perf_counter() - t_total
        manifest = reports.failure_manifest(asset_id, cfg, source_files, primary_name,
                                            errors, warnings, timings)
        reports.write_json(out_dir / "asset_manifest.json", manifest)
        report = reports.build_report(
            asset_id=asset_id, group_dir=str(group_src), status="failed", cfg=cfg,
            inspection={}, cleanup_ops=[], normalize={}, exported={},
            validation={"ok": False, "checks": [], "errors": [msg]},
            timings=timings, warnings=warnings, errors=errors,
            blender_version=bpy.app.version_string)
        reports.write_json(out_dir / "asset_report.json", report)
        reports.write_json(result_path, {
            "ok": False, "status": "failed", "asset_id": asset_id,
            "output_dir": str(out_dir), "stage": stage, "error": str(exc),
            "trace_tail": tb[-1200:], "warnings": warnings, "errors": errors,
            "total_seconds": timings["total"],
        })
        log.close()

    try:
        log(f"=== SPATAIL Asset Factory worker: {asset_id} ===")
        log(f"Blender {bpy.app.version_string} | bounds_mode={cfg['bounds_mode']} "
            f"origin_mode={cfg['origin_mode']} target={cfg['target_bounds_m']}")

        if not group_src.exists():
            raise RuntimeError(f"asset group folder not found: {group_src}")

        # 2) optional scratch copy
        use_scratch = (args["use_scratch"] if args["use_scratch"] is not None
                       else bool(worker_cfg.get("copy_assets_to_local_scratch", True)))
        work_dir = group_src
        if use_scratch:
            try:
                work_dir = _stage_to_scratch(group_src, asset_id, worker_cfg, log)
            except Exception as e:  # noqa: BLE001 — scratch is an optimisation, not required
                warnings.append(f"scratch copy failed, using source in place: {e}")

        files = importers.find_source_files(work_dir)
        if not files:
            raise RuntimeError(f"no importable source files in {group_src} "
                               f"(supported: {', '.join(config.SUPPORTED_EXTS)})")
        primary = importers.choose_primary(files, asset_id)
        primary_name = primary.name
        log(f"primary source: {primary_name}  ({len(files)} importable file(s))")

        # 3) import (hard)
        _clear_scene()
        t = time.perf_counter()
        objs = importers.import_file(primary)
        timings["import"] = time.perf_counter() - t
        log(f"imported {len(objs)} object(s) in {timings['import']:.2f}s")

        # 4) inspect (read-only)
        t = time.perf_counter()
        insp = inspect_asset.inspect(objs, group_name=asset_id,
                                     source_files=source_files, primary_source=primary_name)
        timings["inspection"] = time.perf_counter() - t
        log(f"inspect: {insp['mesh_count']} mesh / {insp['triangle_count']} tris / "
            f"{insp['material_count']} mats / bbox {insp['original_bbox_m']['size']}")

        # 5) conservative cleanup (soft sub-steps)
        t = time.perf_counter()
        meshes, cleanup_ops, cleanup_warns = cleanup_mod.cleanup(objs, asset_id)
        warnings.extend(cleanup_warns)
        timings["cleanup"] = time.perf_counter() - t
        if not meshes:
            raise RuntimeError("no mesh geometry survived cleanup")
        log(f"cleanup: {len(cleanup_ops)} ops, {len(cleanup_warns)} warning(s)")

        # 6/7) normalize bounds + origin (hard)
        t = time.perf_counter()
        norm = normalize_bounds.normalize(
            meshes, cfg["target_bounds_m"], cfg["bounds_mode"], cfg["origin_mode"])
        timings["normalize"] = time.perf_counter() - t
        log(f"normalize: scale x{norm['scale_factor_applied']} -> final "
            f"{norm['final_bbox']['size']} fits={norm['fits_in_target_bounds']}")
        if not norm["fits_in_target_bounds"]:
            warnings.append(
                f"final bounds {norm['final_bbox']['size']} exceed target "
                f"{cfg['target_bounds_m']} (bounds_mode={cfg['bounds_mode']})")

        exported = {}

        # 8) optional collision proxy (soft)
        if cfg.get("generate_collision_proxy"):
            try:
                proxy = export_asset.export_collision_proxy(norm["final_bbox"], out_dir, asset_id)
                exported.update(proxy)
                log(f"collision proxy: {proxy.get('collision_glb')}")
            except Exception as e:  # noqa: BLE001
                warnings.append(f"collision proxy failed: {e}")

        # 9) optional LODs (soft)
        if cfg.get("generate_lods"):
            try:
                lods = export_asset.export_lods(meshes, out_dir, asset_id)
                exported["lods"] = lods
                log(f"LODs: {list(lods)}")
            except Exception as e:  # noqa: BLE001
                warnings.append(f"LOD generation failed: {e}")

        # 10) preview (soft, QA-only)
        if cfg.get("generate_preview", True):
            t = time.perf_counter()
            try:
                pv = preview_mod.render_preview(
                    out_dir / "preview.png", norm["final_bbox"],
                    engine=worker_cfg.get("preview_engine", "workbench"),
                    resolution=int(worker_cfg.get("preview_resolution", 768)),
                    use_gpu=bool(worker_cfg.get("use_gpu_for_preview", True)))
                if pv:
                    exported["preview"] = "preview.png"
                    log(f"preview rendered: {pv}")
                else:
                    warnings.append("preview render produced no file")
            except Exception as e:  # noqa: BLE001
                warnings.append(f"preview failed: {e}")
            timings["preview"] = time.perf_counter() - t

        # 11) export normalized GLB (hard) — re-select only the cleaned meshes first
        t = time.perf_counter()
        glb_info = export_asset.export_normalized(meshes, out_dir, asset_id)
        timings["export"] = time.perf_counter() - t
        exported["glb"] = glb_info["glb"]
        log(f"exported {glb_info['glb']} ({glb_info['glb_bytes']} bytes) in "
            f"{timings['export']:.2f}s")

        # optional RealityKit USDZ (iOS delivery format) — config or --export-usdz flag
        if args.get("export_usdz") or cfg.get("export_usdz"):
            usdz = out_dir / f"{asset_id}.normalized.usdz"
            if export_asset.export_usdz(meshes, usdz):
                exported["usdz"] = usdz.name
                log(f"exported {usdz.name} ({usdz.stat().st_size} bytes)")
            else:
                warnings.append("USDZ export failed")

        exported["manifest"] = "asset_manifest.json"
        exported["report"] = "asset_report.json"

        # 12/13) manifest + report + self-validation
        timings["total"] = time.perf_counter() - t_total
        self_val = validation.validate_asset_output(out_dir, asset_id, cfg,
                                                    final_bbox=norm["final_bbox"],
                                                    require_reports=False)

        manifest = reports.build_manifest(
            asset_id=asset_id, source_files=source_files, primary_source=primary_name,
            status="success", cfg=cfg,
            original_size=norm["original_bbox"]["size"],
            final_size=norm["final_bbox"]["size"],
            scale_factor=norm["scale_factor_applied"], inspection=insp,
            exported=exported, timings=timings, warnings=warnings, errors=errors)
        reports.write_json(out_dir / "asset_manifest.json", manifest)

        report = reports.build_report(
            asset_id=asset_id, group_dir=str(group_src), status="success", cfg=cfg,
            inspection=insp, cleanup_ops=cleanup_ops, normalize=norm,
            exported=exported, validation=self_val, timings=timings,
            warnings=warnings, errors=errors, blender_version=bpy.app.version_string)
        reports.write_json(out_dir / "asset_report.json", report)

        reports.write_json(result_path, {
            "ok": True, "status": "success", "asset_id": asset_id,
            "output_dir": str(out_dir), "glb": glb_info["glb_path"],
            "preview": str(out_dir / "preview.png") if exported.get("preview") else "",
            "manifest": str(out_dir / "asset_manifest.json"),
            "report": str(out_dir / "asset_report.json"),
            "final_bounds": reports._bounds_xyz(norm["final_bbox"]["size"]),
            "triangle_count": insp["triangle_count"],
            "total_seconds": timings["total"],
            "self_validation_ok": self_val["ok"],
            "warnings": warnings, "errors": errors,
        })
        log(f"=== DONE {asset_id}: success in {timings['total']:.2f}s "
            f"(self-validation {'OK' if self_val['ok'] else 'FAILED'}) ===")
        log.close()

    except Exception as e:  # noqa: BLE001 — translate any hard failure into a clean report
        stage = "pipeline"
        _finish_failure(stage, e)


main()
