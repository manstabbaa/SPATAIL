"""plan_adapter.py — AssetRequest → a build plan the headless driver consumes.

The driver (pipeline/blender/spatail_build_from_plan_driver.py) authors in
CENTIMETRES, +Z up, centred on the origin in X/Y — the exact shape
engineexplainer/.../manual_segment.build_plan_from_segment emits and
spatail_model_from_primitives.build_from_plan constructs. This module turns one
AssetRequest into that plan with deterministic primitive geometry (a hero body +
named sub-parts, or a comparison panel, etc.), so a real Blender build can run
WITHOUT an LLM. Geometry here is structural, not visually accurate — accuracy is
a later upgrade (an LLM-authored plan reusing manual_segment.SYSTEM_PROMPT).

content_key() is the AssetCache key: a content hash of the request + plan +
factory version, so identical requests skip regeneration and a changed adapter
invalidates the cache.
"""
from __future__ import annotations

import hashlib
import json
import re

# Bump when the adapter's geometry/semantics change so stale cache entries miss.
FACTORY_VERSION = "1"

_WORD_RE = re.compile(r"[^a-z0-9]+")


def _slug(s: str) -> str:
    return _WORD_RE.sub("_", (s or "").lower()).strip("_") or "asset"


def _box(name: str, role: str, size, loc) -> dict:
    return {"name": name, "role": role, "primitive": "box",
            "size": [float(v) for v in size], "location": [float(v) for v in loc],
            "aliases": [name.replace("_", " ")]}


def request_to_plan(req, *, subject: str) -> dict:
    """Build a cm, Z-up, origin-centred primitive plan for one AssetRequest."""
    aid = _slug(req.assetId)
    role = req.semanticRole

    if role == "comparison_item":
        w, d, h = 80.0, 8.0, 50.0
        parts = [_box(f"{aid}_panel", "panel", [w, d, h], [0, 0, h / 2]),
                 _box(f"{aid}_stand", "base", [w * 0.4, d * 1.6, 4.0], [0, 0, 2.0])]
    elif role == "context_environment":
        parts = [_box(f"{aid}_ground", "environment", [120.0, 120.0, 2.0], [0, 0, 1.0])]
    elif role == "core_structure":
        parts = [_box(f"{aid}_body", "core_structure", [50.0, 35.0, 30.0], [0, 0, 15.0]),
                 _box(f"{aid}_detail_a", "part", [12.0, 12.0, 12.0], [16.0, 0.0, 36.0]),
                 _box(f"{aid}_detail_b", "part", [12.0, 12.0, 12.0], [-16.0, 0.0, 36.0])]
    else:  # component | label_target | anything else
        parts = [_box(f"{aid}_body", role or "part", [16.0, 16.0, 16.0], [0, 0, 8.0])]

    return {
        "assetId": aid,
        "kind": f"{subject} :: {req.assetId}",
        "units": "cm",
        "up_axis": "z",
        "parts": parts,
        "groups": [],
        "assembly_order": [p["name"] for p in parts],
        "director_hints": {
            "asset_kind": subject,
            "semantic_role": role,
            "background_default": "#F5F4EF",
            "preferred_camera_presets": ["hero_threequarter"],
        },
    }


def content_key(req, plan: dict) -> str:
    payload = {
        "v": FACTORY_VERSION,
        "assetId": req.assetId,
        "variants": sorted(req.requiredVariants or []),
        "animations": sorted(req.requiredAnimations or []),
        "plan": plan,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def predict_bbox_from_plan(plan: dict) -> dict:
    """Pure-Python bbox the real build would produce: cm extents → metres, Z-up,
    in the driver's engine_bbox shape {min,max,size,center}. Lets dry-run emit
    complete metadata with no Blender."""
    import math
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for p in plan.get("parts", []):
        loc = p.get("location", [0, 0, 0])
        size = p.get("size", [0, 0, 0])
        for i in range(3):
            lo[i] = min(lo[i], loc[i] - size[i] / 2.0)
            hi[i] = max(hi[i], loc[i] + size[i] / 2.0)
    if lo[0] == math.inf:
        lo, hi = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    cm2m = 0.01
    mn = [round(v * cm2m, 4) for v in lo]
    mx = [round(v * cm2m, 4) for v in hi]
    size = [round(mx[i] - mn[i], 4) for i in range(3)]
    center = [round((mx[i] + mn[i]) / 2.0, 4) for i in range(3)]
    return {"min": mn, "max": mx, "size": size, "center": center}
