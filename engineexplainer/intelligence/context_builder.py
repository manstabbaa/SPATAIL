"""Loads the world state — part registry, animation library, recent
history — into one dict that every LLM call sees. Single source of truth.

Asset-aware: takes an `asset_id` so the orchestrator can produce contracts
that reference the right meshes + animations for whichever asset the web
runtime currently has loaded. Without this, asking a fan a question
produces an engine contract that does nothing on the fan.
"""

from __future__ import annotations

import json
from pathlib import Path

ASSET_ROOT = Path(__file__).resolve().parent.parent / "engine"


# Per-asset file layout. Keys are the asset_id the web sends; values
# tell the loader which JSONs to read for that asset. To add a new asset:
#   1. Author the GLB into engine/<asset_id>.glb
#   2. Drop <asset_id>_part_registry.json + <asset_id>_animation_library.json
#      next to it (same schema as the engine's part_registry / animation_library)
#   3. Add an entry below
#   4. Web side: add the asset to ASSETS in main.js
ASSET_REGISTRY = {
    "engine": {
        "glb": "v8_engine.glb",
        "part_registry": "part_registry.json",
        "animation_library": "animation_library.json",
    },
    "fan": {
        "glb": "fan.glb",
        "part_registry": "fan_part_registry.json",
        "animation_library": "fan_animation_library.json",
    },
}


def _resolve_asset(asset_id: str | None) -> dict:
    """Pick the per-asset file paths. Defaults to engine for back-compat."""
    if not asset_id:
        asset_id = "engine"
    asset_id = asset_id.lower()
    if asset_id not in ASSET_REGISTRY:
        # Unknown asset → fall back to engine but flag it
        print(f"[context_builder] WARN: unknown asset_id={asset_id!r}; falling back to engine")
        asset_id = "engine"
    return {"asset_id": asset_id, **ASSET_REGISTRY[asset_id]}


def load_part_registry(asset_id: str | None = None, path: Path | None = None) -> dict:
    if path is None:
        cfg = _resolve_asset(asset_id)
        path = ASSET_ROOT / cfg["part_registry"]
    if not path.exists():
        # Authoring hasn't run yet — return an empty registry so the
        # rest of the pipeline can still smoke-test.
        return {"asset": path.name, "parts": {}, "kinematicGroups": []}
    return json.loads(path.read_text())


def load_animation_library(asset_id: str | None = None, path: Path | None = None) -> dict:
    if path is None:
        cfg = _resolve_asset(asset_id)
        path = ASSET_ROOT / cfg["animation_library"]
    if not path.exists():
        return {"asset": path.name, "animations": {}}
    return json.loads(path.read_text())


def build_context(prompt: str,
                  history: list[str] | None = None,
                  asset_id: str | None = None) -> dict:
    """Assemble everything the agent stack needs into one flat dict.

    asset_id selects which per-asset part_registry + animation_library to
    load — the orchestrator threads it through from /api/ask so the LLM
    sees the right meshes and animations for the currently-loaded asset.
    """
    cfg = _resolve_asset(asset_id)
    return {
        "asset_id": cfg["asset_id"],
        "asset_glb": cfg["glb"],
        "prompt": prompt,
        "history": list(history or [])[-3:],
        "part_registry": load_part_registry(cfg["asset_id"]),
        "animation_library": load_animation_library(cfg["asset_id"]),
        "camera_presets": [
            "hero_threequarter", "hero_front", "topdown", "section_side", "cylinder_close",
        ],
        "design_invariants": {
            "max_panels_visible": 1,
            "beat_count_recommended": [3, 7],
            "narration_words_per_sec": 2.2,
        },
    }
