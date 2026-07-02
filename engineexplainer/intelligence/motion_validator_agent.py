"""Motion-validator orchestrator agent.

Wraps spatail_motion_validator as a callable agent inside the orchestrator
pipeline. Two execution modes:

  1. INPROC: when the orchestrator is itself running inside Blender (rare),
     directly import the validator script and call validate_asset().

  2. SUBPROCESS: when the orchestrator is the standalone HTTP server (the
     normal case), shell out to Blender in background mode with the
     validator script and read its JSON output. Slow (~6-12s per clip) but
     deterministic and isolates the validator from the running web session.

The validator's PASS/WARN/FAIL verdict gates publishing of the asset's
GLB to the web runtime. Callers receive the same SummaryReport every time
regardless of execution mode.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SCRIPT = REPO_ROOT / "pipeline" / "blender" / "spatail_motion_validator.py"


@dataclass
class MotionValidationFinding:
    clip: str
    verdict: str        # "PASS" | "WARN" | "FAIL"
    motion_consistency_score: float
    stationary_but_should_have_moved: list[str]
    moved_but_not_in_group: list[str]
    contact_sheet: str | None


@dataclass
class MotionValidationReport:
    asset_id: str
    overall_verdict: str    # "PASS" if every clip is PASS, else "WARN" / "FAIL"
    clips: list[MotionValidationFinding]
    out_root: str

    def is_ok(self) -> bool:
        return self.overall_verdict == "PASS"


# ---------------------------------------------------------------------------
# Public entry — pick INPROC if bpy is importable, else SUBPROCESS
# ---------------------------------------------------------------------------

def validate(asset_id: str,
             asset_glb: str,
             registry_path: str,
             anim_library_path: str,
             blend_path: str | None = None,
             out_root: str = r"C:/tmp/motion_validate") -> MotionValidationReport:
    try:
        import bpy  # noqa: F401  — only succeeds inside Blender
        return _run_inproc(asset_id, asset_glb, registry_path, anim_library_path,
                           blend_path, out_root)
    except ImportError:
        return _run_subprocess(asset_id, asset_glb, registry_path, anim_library_path,
                               blend_path, out_root)


# ---------------------------------------------------------------------------
# INPROC mode — used when this module is imported by Blender's Python
# ---------------------------------------------------------------------------

def _run_inproc(asset_id, asset_glb, registry_path, anim_library_path,
                blend_path, out_root) -> MotionValidationReport:
    import bpy
    if blend_path:
        bpy.ops.wm.open_mainfile(filepath=blend_path)
    # Execute the validator script in the current namespace
    g = {"__file__": str(PIPELINE_SCRIPT)}
    exec(PIPELINE_SCRIPT.read_text(encoding="utf-8"), g)
    summary = g["validate_asset"](
        asset_id=asset_id, asset_glb=asset_glb,
        registry_path=registry_path, anim_library_path=anim_library_path,
        out_root=out_root,
    )
    return _summary_to_report(asset_id, summary, out_root)


# ---------------------------------------------------------------------------
# SUBPROCESS mode — run Blender headlessly with the validator script
# ---------------------------------------------------------------------------

BLENDER_EXE = os.environ.get(
    "ENGINEEXPLAINER_BLENDER_EXE",
    r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
)


def _run_subprocess(asset_id, asset_glb, registry_path, anim_library_path,
                    blend_path, out_root) -> MotionValidationReport:
    """Invoke Blender in --background mode with a tiny driver script that
    runs the validator and prints the resulting JSON to stdout."""
    blend_path = blend_path or _guess_blend_path(asset_id)
    if not Path(BLENDER_EXE).exists():
        raise FileNotFoundError(
            f"Blender executable not found at {BLENDER_EXE}. Set "
            "ENGINEEXPLAINER_BLENDER_EXE to override.")

    # Write a driver script into a temp file — we pass it to Blender via -P
    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        driver_path = f.name
        f.write(_DRIVER_TEMPLATE.format(
            pipeline_script=str(PIPELINE_SCRIPT).replace("\\", "/"),
            asset_id=asset_id,
            asset_glb=asset_glb.replace("\\", "/"),
            registry_path=registry_path.replace("\\", "/"),
            anim_library_path=anim_library_path.replace("\\", "/"),
            out_root=out_root.replace("\\", "/"),
        ))

    cmd = [BLENDER_EXE, "--background", blend_path, "--python", driver_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    os.unlink(driver_path)

    # The driver writes JSON to a known sidecar file (stdout is noisy)
    sidecar = Path(out_root) / asset_id / "_summary.json"
    if not sidecar.exists():
        raise RuntimeError(
            f"Validator did not produce {sidecar}. Blender stderr:\n"
            f"{proc.stderr[-2000:]}")
    summary = json.loads(sidecar.read_text(encoding="utf-8"))
    return _summary_to_report(asset_id, summary, out_root)


_DRIVER_TEMPLATE = """
import bpy, json
exec(open(r"{pipeline_script}").read())
validate_asset(
    asset_id={asset_id!r},
    asset_glb=r"{asset_glb}",
    registry_path=r"{registry_path}",
    anim_library_path=r"{anim_library_path}",
    out_root=r"{out_root}",
)
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _guess_blend_path(asset_id: str) -> str:
    p = REPO_ROOT.parent / "assets_authoring" / f"{asset_id}.blend"
    if not p.exists():
        raise FileNotFoundError(f"No .blend at {p} for asset_id={asset_id!r}")
    return str(p)


def _summary_to_report(asset_id, summary, out_root) -> MotionValidationReport:
    clips = []
    worst = "PASS"
    rank = {"PASS": 0, "WARN": 1, "FAIL": 2}
    for c in summary.get("clips", []):
        v = c.get("verdict", "WARN").upper()
        if rank.get(v, 1) > rank.get(worst, 0):
            worst = v
        clips.append(MotionValidationFinding(
            clip=c.get("clip", "?"), verdict=v,
            motion_consistency_score=float(c.get("motion_consistency_score", 0.0)),
            stationary_but_should_have_moved=list(c.get("stationary_but_should_have_moved", [])),
            moved_but_not_in_group=list(c.get("moved_but_not_in_group", [])),
            contact_sheet=c.get("contact_sheet"),
        ))
    return MotionValidationReport(
        asset_id=asset_id,
        overall_verdict=worst,
        clips=clips,
        out_root=str(Path(out_root) / asset_id),
    )
