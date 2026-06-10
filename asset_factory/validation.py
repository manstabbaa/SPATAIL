"""validation.py — verify a processed asset honours the contract.

bpy-free, so the worker manager can re-run the exact same checks the Blender
worker ran. The single most important check is `final bounds fit inside target
bounds`; the second is `origin mode is correct` (bottom sits on z=0 for
bottom_center, bbox centre at origin for center).
"""
from __future__ import annotations

from pathlib import Path

from config import ALLOWED_ORIGIN_MODES, BOUNDS_TOLERANCE_M  # type: ignore


def within_bounds(final_size, target: dict, tol: float = BOUNDS_TOLERANCE_M) -> bool:
    """True if every axis of `final_size` is <= the target bound (+ tolerance)."""
    if not final_size:
        return False
    s = list(final_size)
    return (s[0] <= target.get("x", 0) + tol
            and s[1] <= target.get("y", 0) + tol
            and s[2] <= target.get("z", 0) + tol)


def _check(name: str, passed: bool, detail: str = "") -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail}


def validate_origin(final_bbox: dict, origin_mode: str,
                    tol: float = max(BOUNDS_TOLERANCE_M, 1e-3)) -> dict:
    """Check the seated origin against the configured mode using the final bbox.

    final_bbox carries {min,center,...} in metres. bottom_center → centre x/y ~ 0
    and min z ~ 0; center → centre ~ 0 on all axes.
    """
    if not final_bbox:
        return _check("origin_mode", False, "no final bbox recorded")
    center = final_bbox.get("center", [0, 0, 0])
    mn = final_bbox.get("min", [0, 0, 0])
    if origin_mode == "bottom_center":
        ok = abs(center[0]) <= tol and abs(center[1]) <= tol and abs(mn[2]) <= tol
        return _check("origin_mode", ok,
                      f"bottom_center: center_xy=({center[0]:.4f},{center[1]:.4f}), "
                      f"min_z={mn[2]:.4f}")
    if origin_mode == "center":
        ok = all(abs(c) <= tol for c in center[:3])
        return _check("origin_mode", ok,
                      f"center: center=({center[0]:.4f},{center[1]:.4f},{center[2]:.4f})")
    return _check("origin_mode", origin_mode in ALLOWED_ORIGIN_MODES,
                  f"unknown origin_mode {origin_mode!r}")


def validate_asset_output(output_dir, asset_id: str, cfg: dict,
                          final_bbox: dict | None = None,
                          require_reports: bool = True) -> dict:
    """Validate one processed asset folder against the configured contract.

    Returns {ok, checks:[...], errors:[...]}. `final_bbox` (if given by the worker)
    enables the origin-mode check; the manager re-reads it from the report.

    `require_reports` gates the manifest/report existence checks. The worker calls
    with it False (it self-checks geometry BEFORE writing those files); the manager
    calls with the default True after the worker exits, as the authoritative gate.
    """
    out = Path(output_dir)
    cfg = cfg or {}
    target = cfg.get("target_bounds_m", {"x": 1, "y": 1, "z": 1})
    checks: list[dict] = []
    errors: list[str] = []

    glb = out / f"{asset_id}.normalized.glb"
    manifest = out / "asset_manifest.json"
    report = out / "asset_report.json"
    preview = out / "preview.png"

    checks.append(_check("glb_exists", glb.exists() and glb.stat().st_size > 0, str(glb)))
    if require_reports:
        checks.append(_check("manifest_exists", manifest.exists(), str(manifest)))
        checks.append(_check("report_exists", report.exists(), str(report)))
    if cfg.get("generate_preview", True):
        checks.append(_check("preview_exists", preview.exists(), str(preview)))

    # Bounds + origin from the final bbox (worker passes it; manager reads report).
    if final_bbox is None:
        final_bbox = _read_final_bbox(report)
    size = (final_bbox or {}).get("size")
    checks.append(_check("final_bounds_within_target", within_bounds(size, target),
                         f"size={size} target={target}"))
    checks.append(validate_origin(final_bbox or {}, cfg.get("origin_mode", "bottom_center")))

    for c in checks:
        if not c["passed"]:
            errors.append(f"{c['name']} failed: {c['detail']}")
    return {"ok": not errors, "checks": checks, "errors": errors}


def _read_final_bbox(report_path: Path) -> dict:
    import json
    try:
        rep = json.loads(Path(report_path).read_text(encoding="utf-8"))
        return (rep.get("normalization") or {}).get("final_bbox", {}) or {}
    except (ValueError, OSError):
        return {}
