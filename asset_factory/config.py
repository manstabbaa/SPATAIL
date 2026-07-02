"""config.py — config + folder-layout resolution for the SPATAIL AI Asset Factory.

This module is deliberately **bpy-free** so it can be imported by both the normal
Python worker manager AND the Blender worker. It owns:

  * the on-disk folder layout (assets_raw / assets_processed / asset_factory/*),
  * the global config  (asset_factory/config/default_asset_factory_config.json),
  * the worker  config (asset_factory/config/worker_config.json),
  * per-asset overrides (assets_raw/<group>/asset_factory_config.json), and
  * the RAM-based default worker count.

Per-asset config overrides the global config key-by-key. Nothing here imports
bpy; the Blender-specific code lives in importers/inspect/cleanup/normalize/
export/preview and is only ever imported by process_one_asset.py inside Blender.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# ── folder layout ────────────────────────────────────────────────────────────
# asset_factory/ lives at the repo root next to assets_raw/ and assets_processed/
# (the existing top-level SPATAIL convention; assets_processed/ is git-ignored).
ASSET_FACTORY_DIR = Path(__file__).resolve().parent
REPO_ROOT = ASSET_FACTORY_DIR.parent

CONFIG_DIR = ASSET_FACTORY_DIR / "config"
CACHE_DIR = ASSET_FACTORY_DIR / "cache"
SCRATCH_DIR = CACHE_DIR / "scratch"
REPORTS_DIR = ASSET_FACTORY_DIR / "reports"
PREVIEWS_DIR = ASSET_FACTORY_DIR / "previews"
WORKERS_DIR = ASSET_FACTORY_DIR / "workers"
LOGS_DIR = ASSET_FACTORY_DIR / "logs"

DEFAULT_CONFIG_PATH = CONFIG_DIR / "default_asset_factory_config.json"
WORKER_CONFIG_PATH = CONFIG_DIR / "worker_config.json"

# Per-asset override file dropped inside an asset group folder.
ASSET_CONFIG_NAME = "asset_factory_config.json"

# Formats Blender can reasonably import, in primary-pick priority order
# (self-contained / texture-bearing first, geometry-only last).
SUPPORTED_EXTS = (".glb", ".gltf", ".fbx", ".obj", ".stl")

ALLOWED_BOUNDS_MODES = ("fit_longest_axis", "fit_inside_box", "stretch_to_box")
ALLOWED_ORIGIN_MODES = ("bottom_center", "center")

# Canonical defaults — written to disk on first run if the files are absent, and
# used as the merge base so a partial config file never drops a required key.
DEFAULT_ASSET_CONFIG = {
    "target_bounds_m": {"x": 1.0, "y": 1.0, "z": 1.0},
    "bounds_mode": "fit_longest_axis",
    "origin_mode": "bottom_center",
    "unit_system": "meters",
    "generate_preview": True,
    "generate_collision_proxy": False,
    "generate_lods": False,
    "export_usdz": False,           # also emit a RealityKit USDZ (iOS delivery format)
}

DEFAULT_WORKER_CONFIG = {
    # "auto" → derive a conservative count from system RAM (see recommended_workers).
    # Set a positive integer to pin it. The --max-workers CLI flag always wins.
    "max_workers": "auto",
    "asset_timeout_seconds": 600,
    "preview_engine": "workbench",       # workbench | eevee | cycles
    "preview_resolution": 768,
    "use_gpu_for_preview": True,
    "copy_assets_to_local_scratch": True,
    "scratch_dir": "asset_factory/cache/scratch",
}

# Floating-point slack (metres) when asserting "fits inside target bounds".
BOUNDS_TOLERANCE_M = 1e-4


# ── folder bootstrap ─────────────────────────────────────────────────────────
def ensure_dirs() -> None:
    """Create the asset_factory/* working folders if they do not exist."""
    for d in (CONFIG_DIR, CACHE_DIR, SCRATCH_DIR, REPORTS_DIR, PREVIEWS_DIR,
              WORKERS_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _write_json(path: Path, obj: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2), encoding="utf-8")


# ── config loaders ───────────────────────────────────────────────────────────
def load_global_config() -> dict:
    """The global asset config, seeded with defaults if the file is missing."""
    ensure_dirs()
    if not DEFAULT_CONFIG_PATH.exists():
        _write_json(DEFAULT_CONFIG_PATH, DEFAULT_ASSET_CONFIG)
        return dict(DEFAULT_ASSET_CONFIG)
    return _merge(DEFAULT_ASSET_CONFIG, _read_json(DEFAULT_CONFIG_PATH))


def load_worker_config() -> dict:
    """The worker/runtime config, seeded with defaults if the file is missing."""
    ensure_dirs()
    if not WORKER_CONFIG_PATH.exists():
        _write_json(WORKER_CONFIG_PATH, DEFAULT_WORKER_CONFIG)
        return dict(DEFAULT_WORKER_CONFIG)
    return _merge(DEFAULT_WORKER_CONFIG, _read_json(WORKER_CONFIG_PATH))


def load_asset_config(group_dir: str | os.PathLike, global_cfg: dict | None = None) -> dict:
    """Merge the global config with a per-asset override file (if present).

    assets_raw/<group>/asset_factory_config.json wins key-by-key over the global
    config. The result is normalised + validated (unknown enum values fall back
    to the default and are surfaced via `_config_warnings`).
    """
    base = dict(global_cfg if global_cfg is not None else load_global_config())
    override_path = Path(group_dir) / ASSET_CONFIG_NAME
    if override_path.exists():
        base = _merge(base, _read_json(override_path))
    return normalize_asset_config(base)


def _merge(base: dict, override: dict) -> dict:
    """Shallow merge with a one-level deep-merge for nested dicts (target_bounds_m)."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            merged = dict(out[k])
            merged.update(v)
            out[k] = merged
        else:
            out[k] = v
    return out


def normalize_asset_config(cfg: dict) -> dict:
    """Coerce/validate an asset config; record any corrections under _config_warnings."""
    cfg = dict(cfg)
    warnings: list[str] = []

    tb = cfg.get("target_bounds_m") or {}
    cfg["target_bounds_m"] = {
        "x": _pos_float(tb.get("x"), 1.0), "y": _pos_float(tb.get("y"), 1.0),
        "z": _pos_float(tb.get("z"), 1.0),
    }

    if cfg.get("bounds_mode") not in ALLOWED_BOUNDS_MODES:
        warnings.append(f"bounds_mode {cfg.get('bounds_mode')!r} invalid; "
                        f"using {DEFAULT_ASSET_CONFIG['bounds_mode']!r}")
        cfg["bounds_mode"] = DEFAULT_ASSET_CONFIG["bounds_mode"]
    if cfg.get("origin_mode") not in ALLOWED_ORIGIN_MODES:
        warnings.append(f"origin_mode {cfg.get('origin_mode')!r} invalid; "
                        f"using {DEFAULT_ASSET_CONFIG['origin_mode']!r}")
        cfg["origin_mode"] = DEFAULT_ASSET_CONFIG["origin_mode"]

    for k in ("generate_preview", "generate_collision_proxy", "generate_lods", "export_usdz"):
        cfg[k] = bool(cfg.get(k, DEFAULT_ASSET_CONFIG[k]))
    cfg["unit_system"] = cfg.get("unit_system") or "meters"
    if warnings:
        cfg["_config_warnings"] = warnings
    return cfg


def _pos_float(v, default: float) -> float:
    try:
        f = float(v)
        return f if f > 0 else default
    except (TypeError, ValueError):
        return default


# ── worker count from RAM ────────────────────────────────────────────────────
def total_ram_gb() -> float:
    """Best-effort total physical RAM in GB, with no hard dependency on psutil."""
    # 1) psutil if available
    try:
        import psutil  # type: ignore
        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        pass
    # 2) Windows: GlobalMemoryStatusEx
    if os.name == "nt":
        try:
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            ms = _MS()
            ms.dwLength = ctypes.sizeof(_MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return ms.ullTotalPhys / (1024 ** 3)
        except Exception:
            pass
    # 3) POSIX: sysconf
    try:
        return (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / (1024 ** 3)
    except (ValueError, OSError, AttributeError):
        pass
    return 0.0


def recommended_workers(ram_gb: float | None = None) -> int:
    """Conservative default worker count keyed to system RAM (per the spec ladder)."""
    gb = total_ram_gb() if ram_gb is None else ram_gb
    if gb <= 0:
        return 2                      # unknown → safest
    if gb < 64:
        return 2
    if gb < 128:
        return 4
    if gb < 256:
        return 8
    return 12


def resolve_max_workers(cli_value: int | None, worker_cfg: dict | None = None) -> int:
    """Precedence: --max-workers CLI > worker_config (positive int) > RAM default."""
    if cli_value and int(cli_value) > 0:
        return int(cli_value)
    cfg = worker_cfg if worker_cfg is not None else load_worker_config()
    mw = cfg.get("max_workers")
    if isinstance(mw, bool):                       # guard: bool is an int subclass
        mw = None
    if isinstance(mw, (int, float)) and int(mw) > 0:
        return int(mw)
    return recommended_workers()


def resolve_blender_exe(cli_value: str | None = None) -> str:
    """Blender path: CLI > $BLENDER_EXE > the repo's pinned 5.1 default."""
    return (cli_value or os.environ.get("BLENDER_EXE")
            or r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")


def scratch_root(worker_cfg: dict | None = None) -> Path:
    """Absolute scratch dir (config value may be repo-relative)."""
    cfg = worker_cfg if worker_cfg is not None else load_worker_config()
    raw = cfg.get("scratch_dir") or "asset_factory/cache/scratch"
    p = Path(raw)
    return p if p.is_absolute() else (REPO_ROOT / p)
