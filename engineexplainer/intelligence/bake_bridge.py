"""Bake-bridge — intercept `bake_animation` actions in a draft contract,
run Blender headlessly to bake the requested motion, re-export the GLB,
and patch the contract so the new clip is referenced as a normal
`play_animation` action by the time the runtime sees it.

Triggered automatically by orchestrator.answer() after the director +
critic produce a draft contract. If a draft contains zero `bake_animation`
actions, this module is a no-op (~0ms overhead). If it contains one or
more, each is dispatched to Blender via subprocess (~6-15s each).

The bridge is asset-aware:
  - Blend path: assets_authoring/<asset_id>.blend
  - GLB path:   engineexplainer/engine/<asset_id>.glb (with engine→v8_engine alias)
  - Axis default: registry.rotation_axis_world

After all bakes succeed, the bridge:
  1. Replaces every `bake_animation` action in the contract with a
     `play_animation` referencing the new clip name
  2. Bumps the contract.meta.glb_version so the web runtime knows
     to re-fetch the GLB before playing the contract
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SCRIPT = REPO_ROOT / "pipeline" / "blender" / "spatail_bake_one_animation.py"
BLENDER_EXE = os.environ.get(
    "ENGINEEXPLAINER_BLENDER_EXE",
    r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
)


def _asset_paths(asset_id: str) -> dict:
    asset_id = asset_id or "fan"
    glb_basename = "v8_engine" if asset_id == "engine" else asset_id
    return {
        "blend_path": str(REPO_ROOT.parent / "assets_authoring" / f"{asset_id}.blend"),
        "glb_path":   str(REPO_ROOT / "engineexplainer" / "engine" / f"{glb_basename}.glb"),
        "registry":   REPO_ROOT / "engineexplainer" / "engine" /
                      (f"{asset_id}_part_registry.json" if asset_id != "engine" else "part_registry.json"),
    }


def _registry(asset_id: str) -> dict:
    p = _asset_paths(asset_id)["registry"]
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _resolve_axis(asset_id: str, declared_axis):
    if declared_axis:
        return declared_axis
    reg = _registry(asset_id)
    return reg.get("rotation_axis_world") or [0, 1, 0]


def _bake_intent_from_action(asset_id: str, action: dict) -> dict:
    paths = _asset_paths(asset_id)
    return {
        "asset_id": asset_id,
        "blend_path": paths["blend_path"],
        "glb_path":   paths["glb_path"],
        "name":       action.get("animation") or action.get("name") or "unnamed_bake",
        "parts":      action.get("parts", []),
        "motion":     action.get("motion", "spin"),
        "axis":       _resolve_axis(asset_id, action.get("axis")),
        "magnitude_m": float(action.get("magnitude_m", 0.04)),
        "cycles_per_loop": int(action.get("cycles_per_loop", 1)),
        "frames": int(action.get("frames", 120)),
    }


def _run_bake_intent(intent: dict) -> dict:
    """Spawn Blender headlessly, run the bake driver, read its result JSON."""
    if not Path(BLENDER_EXE).exists():
        raise FileNotFoundError(f"Blender not found at {BLENDER_EXE}")
    if not Path(intent["blend_path"]).exists():
        raise FileNotFoundError(f"Blend not found at {intent['blend_path']}")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        spec_path = f.name
        json.dump(intent, f)

    cmd = [BLENDER_EXE, "--background", "--python", str(PIPELINE_SCRIPT), "--", spec_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    os.unlink(spec_path)

    sidecar = Path(intent["blend_path"] + ".bake_result.json")
    if not sidecar.exists():
        raise RuntimeError(
            f"bake driver did not write result sidecar. Blender stderr tail:\n"
            f"{proc.stderr[-1500:]}")
    result = json.loads(sidecar.read_text(encoding="utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"bake failed: {result.get('error')}\n{result.get('trace','')[:1500]}")
    return result


def maybe_bake_from_contract(contract: dict) -> dict:
    """Walk the draft contract for bake_animation actions. For each one,
    run the bake, patch the action into a regular play_animation. Updates
    contract in-place, returns a dict of {baked_clip_name: result}."""
    asset_id = contract.get("meta", {}).get("asset_id") or contract.get("meta", {}).get("id") or "fan"
    baked = {}
    bumped = False
    for beat in contract.get("beats", []):
        new_actions = []
        for action in beat.get("actions", []):
            if action.get("type") == "bake_animation":
                intent = _bake_intent_from_action(asset_id, action)
                t0 = time.time()
                result = _run_bake_intent(intent)
                dt = time.time() - t0
                baked[result["action_name"]] = {**result, "elapsed_s": round(dt, 1)}
                bumped = True
                # Replace this action with a play_animation for the freshly-baked clip
                new_actions.append({
                    "type": "play_animation",
                    "animation": result["action_name"],
                    "from": 0, "to": 1, "rate": 1.0, "loop": False,
                    "startAt": action.get("startAt", 0),
                })
            else:
                new_actions.append(action)
        beat["actions"] = new_actions

    if bumped:
        contract.setdefault("meta", {})["glb_version"] = int(time.time())
        contract.setdefault("meta", {})["baked_clips"] = list(baked.keys())
    return baked
