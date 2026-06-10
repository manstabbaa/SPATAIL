"""reports.py — assemble the manifest / report / batch-report JSON documents.

bpy-free. The Blender worker fills these dicts from inspection + normalization
results; the worker manager aggregates per-asset results into the batch report.
Keeping the document shapes here (rather than scattered through the worker) makes
the public contract — asset_manifest.json, asset_report.json, batch_report.json —
a single source of truth.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path, obj: dict) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    tmp.replace(p)
    return str(p)


def _bounds_xyz(size) -> dict:
    s = list(size or [0.0, 0.0, 0.0])
    return {"x": round(float(s[0]), 6), "y": round(float(s[1]), 6),
            "z": round(float(s[2]), 6)}


def build_manifest(*, asset_id: str, source_files: list, primary_source: str,
                   status: str, cfg: dict, original_size, final_size,
                   scale_factor: float, inspection: dict, exported: dict,
                   timings: dict, warnings: list, errors: list) -> dict:
    """The compact, stable contract SPATAIL consumes (asset_manifest.json)."""
    tb = cfg.get("target_bounds_m", {})
    return {
        "asset_id": asset_id,
        "source_files": list(source_files or []),
        "primary_source_file": primary_source,
        "status": status,
        "unit_system": cfg.get("unit_system", "meters"),
        "target_bounds_m": {"x": tb.get("x", 1.0), "y": tb.get("y", 1.0),
                            "z": tb.get("z", 1.0)},
        "bounds_mode": cfg.get("bounds_mode"),
        "origin_mode": cfg.get("origin_mode"),
        "original_bounds_m": _bounds_xyz(original_size),
        "final_bounds_m": _bounds_xyz(final_size),
        "scale_factor_applied": round(float(scale_factor), 6),
        "triangle_count": int(inspection.get("triangle_count", 0)),
        "vertex_count": int(inspection.get("vertex_count", 0)),
        "mesh_count": int(inspection.get("mesh_count", 0)),
        "material_count": int(inspection.get("material_count", 0)),
        "has_uvs": bool(inspection.get("has_uvs", False)),
        "has_materials": bool(inspection.get("has_materials", False)),
        "exported_files": exported,
        "timings_seconds": _round_timings(timings),
        "warnings": list(warnings or []),
        "errors": list(errors or []),
    }


def build_report(*, asset_id: str, group_dir: str, status: str, cfg: dict,
                 inspection: dict, cleanup_ops: list, normalize: dict,
                 exported: dict, validation: dict, timings: dict,
                 warnings: list, errors: list, blender_version: str = "") -> dict:
    """The deep QA document (asset_report.json) — everything we observed + did."""
    return {
        "asset_id": asset_id,
        "group_dir": group_dir,
        "status": status,
        "generated_at": now_iso(),
        "blender_version": blender_version,
        "config_resolved": {k: v for k, v in cfg.items() if not k.startswith("_")},
        "config_warnings": cfg.get("_config_warnings", []),
        "inspection": inspection,
        "cleanup_operations": cleanup_ops,
        "normalization": normalize,
        "exported_files": exported,
        "validation": validation,
        "timings_seconds": _round_timings(timings),
        "warnings": list(warnings or []),
        "errors": list(errors or []),
    }


def failure_manifest(asset_id: str, cfg: dict, source_files, primary_source,
                     errors, warnings, timings) -> dict:
    """A minimal manifest for an asset that failed before producing geometry."""
    return build_manifest(
        asset_id=asset_id, source_files=source_files or [],
        primary_source=primary_source or "", status="failed", cfg=cfg,
        original_size=[0, 0, 0], final_size=[0, 0, 0], scale_factor=0.0,
        inspection={}, exported={}, timings=timings or {},
        warnings=warnings or [], errors=errors or [])


def _round_timings(t: dict) -> dict:
    return {k: round(float(v), 4) for k, v in (t or {}).items()}


# ── batch report (worker manager side) ───────────────────────────────────────
def build_batch_report(*, input_dir: str, output_dir: str, max_workers: int,
                       total_seconds: float, results: list) -> dict:
    """Aggregate per-asset results into assets_processed/batch_report.json."""
    succeeded = [r for r in results if r.get("status") == "success"]
    failed = [r for r in results if r.get("status") == "failed"]
    skipped = [r for r in results if r.get("status") == "skipped"]
    warn_count = sum(len(r.get("warnings", [])) for r in results)
    return {
        "generated_at": now_iso(),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "max_workers_used": int(max_workers),
        "total_processing_seconds": round(float(total_seconds), 3),
        "total_asset_groups": len(results),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "skipped": len(skipped),
        "warnings_count": warn_count,
        "assets": [
            {
                "asset_id": r.get("asset_id"),
                "status": r.get("status"),
                "output_path": r.get("output_dir", ""),
                "glb": r.get("glb", ""),
                "final_bounds_m": r.get("final_bounds", {}),
                "triangle_count": r.get("triangle_count", 0),
                "total_seconds": round(float(r.get("total_seconds", 0.0)), 3),
                "errors": r.get("errors", []),
                "warnings": r.get("warnings", []),
            }
            for r in results
        ],
    }


def console_summary(batch: dict) -> str:
    """The human-facing final summary printed by the worker manager."""
    lines = [
        "",
        "SPATAIL Asset Factory complete.",
        f"Processed: {batch.get('total_asset_groups', 0)}",
        f"Succeeded: {batch.get('succeeded', 0)}",
        f"Failed: {batch.get('failed', 0)}",
    ]
    if batch.get("skipped"):
        lines.append(f"Skipped: {batch['skipped']}")
    if batch.get("warnings_count"):
        lines.append(f"Warnings: {batch['warnings_count']}")
    lines += [
        f"Output: {batch.get('output_dir', '')}",
        f"Batch report: {Path(batch.get('output_dir', '')) / 'batch_report.json'}",
    ]
    failed = [a for a in batch.get("assets", []) if a.get("status") == "failed"]
    if failed:
        lines.append("Failed assets:")
        for a in failed:
            why = (a.get("errors") or ["(no error captured)"])[0]
            lines.append(f"  - {a.get('asset_id')}: {why}")
    return "\n".join(lines)
