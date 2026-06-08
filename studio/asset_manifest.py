"""asset_manifest.py — the CONTRACT between Blender (asset producer) and the
composer (experience designer). Both import this, so they agree on an asset's
COMPONENTS, their PLACEMENT, and the MOTION each implies.

Blender builds named parts and emits an AssetManifest ({assetId}_manifest.json):
each part carries its name, a semantic ROLE (inferred from the name), and its
PLACEMENT (pivot, size, bbox) in the asset's rendered Y-up frame. The composer
reads the manifest and maps each component to the right mechanic — piston ->
reciprocate along its axis, crankshaft -> spin about its axis — targeting the part
by name; the runtime then animates that named sub-entity.

One schema, one role vocabulary, one role->motion map. Pure stdlib so it imports
anywhere (inside Blender, the server, the composer).
"""
from __future__ import annotations

import re

SCHEMA = "spatail-asset-manifest/1"

# Semantic component roles, inferred from a part's name (longest/most-specific wins).
ROLE_KEYWORDS = {
    "crank":    ["crankshaft", "crank"],
    "camshaft": ["camshaft"],
    "piston":   ["piston"],
    "rod":      ["conrod", "connecting", "pushrod", "rod"],
    "valve":    ["valve", "tappet", "lifter"],
    "gear":     ["gear", "cog", "sprocket", "pinion"],
    "wheel":    ["wheel", "tire", "tyre"],
    "fan":      ["fan", "blade", "propeller", "prop", "rotor", "impeller", "turbine"],
    "shaft":    ["driveshaft", "axle", "spindle", "shaft"],
    "pendulum": ["pendulum", "bob"],
    "spring":   ["spring", "coil"],
    "belt":     ["belt", "chain", "timing"],
    "rotor_disc": ["disc", "disk", "flywheel"],
    "body":     ["body", "block", "case", "casing", "housing", "frame", "base",
                 "engine", "mount", "cover", "manifold"],
}

# Role -> the motion mechanic + axis it should get. Axes are in the asset's RENDERED
# Y-up frame (USDZ export turns Blender +Z-up into +Y-up), so "up" == y.
ROLE_MOTION: dict[str, dict] = {
    "piston":   {"mechanic": "reciprocate", "axis": "y", "period_s": 0.5},
    "valve":    {"mechanic": "reciprocate", "axis": "y", "period_s": 0.5},
    "rod":      {"mechanic": "reciprocate", "axis": "y", "period_s": 0.5},
    "spring":   {"mechanic": "reciprocate", "axis": "y", "period_s": 1.0},
    "crank":    {"mechanic": "spin", "axis": "x", "rpm": 40},
    "camshaft": {"mechanic": "spin", "axis": "x", "rpm": 20},
    "shaft":    {"mechanic": "spin", "axis": "x", "rpm": 30},
    "gear":     {"mechanic": "spin", "axis": "z", "rpm": 25},
    "wheel":    {"mechanic": "spin", "axis": "x", "rpm": 20},
    "fan":      {"mechanic": "spin", "axis": "z", "rpm": 80},
    "rotor_disc": {"mechanic": "spin", "axis": "y", "rpm": 30},
    "belt":     {"mechanic": "flow", "axis": "y"},
    "pendulum": {"mechanic": "oscillate", "axis": "x"},
    # body/block/housing and unknown "part" -> no intrinsic motion (the static frame)
}


def infer_role(name: str) -> str:
    """Map a part name to a semantic role. Strips trailing instance suffixes
    (piston_1, gear.003) before matching."""
    n = re.sub(r"[._]\d+$", "", (name or "").strip().lower())
    n = n.replace("-", "_")
    for role, kws in ROLE_KEYWORDS.items():
        if any(k in n for k in kws):
            return role
    return "part"


def motion_for(role: str) -> dict | None:
    return ROLE_MOTION.get(role)


def enrich_parts(raw_parts: list[dict]) -> list[dict]:
    """Attach role + motion to Blender's per-part geometry facts (name/pivot/size).
    This is the contract instance the composer consumes."""
    out = []
    for p in raw_parts or []:
        role = infer_role(p.get("name", ""))
        out.append({
            "name": p.get("name"),
            "role": role,
            "pivot_m": p.get("pivot_m", [0.0, 0.0, 0.0]),
            "size_m": p.get("size_m"),
            "motion": motion_for(role),          # {mechanic, axis, ...} or None
        })
    return out


def moving_parts(parts: list[dict] | None) -> list[dict]:
    """Parts that imply intrinsic motion (the composer animates these per-component)."""
    return [p for p in (parts or []) if p.get("motion")]


if __name__ == "__main__":
    import json
    demo = [{"name": "engine_block", "pivot_m": [0, 0.1, 0], "size_m": [0.4, 0.3, 0.4]},
            {"name": "piston_1", "pivot_m": [-0.1, 0.2, 0], "size_m": [0.05, 0.1, 0.05]},
            {"name": "piston_2", "pivot_m": [0.1, 0.2, 0], "size_m": [0.05, 0.1, 0.05]},
            {"name": "crankshaft", "pivot_m": [0, 0.05, 0], "size_m": [0.3, 0.05, 0.05]},
            {"name": "fan_blade", "pivot_m": [0, 0.1, 0.2], "size_m": [0.2, 0.2, 0.02]}]
    enriched = enrich_parts(demo)
    print(json.dumps(enriched, indent=2))
    print("moving:", [(p["name"], p["motion"]["mechanic"]) for p in moving_parts(enriched)])
