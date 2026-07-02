"""build_library.py — expand catalog.py into JSON manifests (pure metadata, no Blender).

    python studio/library/build_library.py

Writes, under public/assets/spatail-library/:
  manifests/<category>.json   one entry per asset (full metadata schema)
  manifests/library.json      master index (per-category counts + flat asset list)
  manifests/strategies.json   the 20 presentation strategies as modules
  manifests/behaviors.json    the 25 reusable animation behaviours
  manifests/schema.json       the per-asset metadata schema (documentation)
  <category>/.gitkeep         so the (still-empty) geometry folders commit

Idempotent: re-running overwrites the manifests from the catalog.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog as cat  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB_DIR = REPO_ROOT / "public" / "assets" / "spatail-library"
MANIFESTS = LIB_DIR / "manifests"

FALLBACK_PRIMITIVES = {
    "cube", "rounded_cube", "sphere", "half_sphere", "cylinder", "cone",
    "capsule", "torus", "plane", "ring", "arrow", "line", "none",
}

REQUIRED_FIELDS = [
    "assetId", "name", "category", "semanticTags", "path", "scaleMeters", "pivot",
    "placementTypes", "representationUses", "supportsHighlight", "supportsTransparency",
    "supportsAnimation", "fallbackPrimitive", "qualityLevel", "license", "assetState",
]


def _asset_metadata(category: str, defaults: dict, aid: str, name: str,
                    tags, overrides: dict) -> dict:
    def pick(key, fallback):
        return overrides.get(key, defaults.get(key, fallback))
    return {
        "assetId": aid,
        "name": name,
        "category": category,
        "semanticTags": list(tags),
        "path": f"{cat.LIBRARY_ROOT_URL}/{category}/{aid}.glb",
        "scaleMeters": pick("scaleMeters", [0.1, 0.1, 0.1]),
        "pivot": pick("pivot", "center"),
        "placementTypes": pick("placementTypes", ["table"]),
        "representationUses": pick("representationUses", []),
        "supportsHighlight": pick("supportsHighlight", True),
        "supportsTransparency": pick("supportsTransparency", False),
        "supportsAnimation": pick("supportsAnimation", False),
        "fallbackPrimitive": pick("fallbackPrimitive", "cube"),
        "qualityLevel": pick("qualityLevel", "starter"),
        "license": pick("license", "internal_generated"),
        "assetState": "metadata",          # flips to "generated"/"cached" when a GLB lands
    }


def _validate(asset: dict, problems: list):
    for f in REQUIRED_FIELDS:
        if f not in asset:
            problems.append(f"{asset.get('assetId','?')}: missing {f}")
    if asset.get("fallbackPrimitive") not in FALLBACK_PRIMITIVES:
        problems.append(f"{asset['assetId']}: unknown fallbackPrimitive {asset.get('fallbackPrimitive')!r}")
    for use in asset.get("representationUses", []):
        if use not in cat.STRATEGY_IDS:
            problems.append(f"{asset['assetId']}: representationUse {use!r} not a known strategy")
    sm = asset.get("scaleMeters")
    if not (isinstance(sm, list) and len(sm) == 3 and all(isinstance(v, (int, float)) for v in sm)):
        problems.append(f"{asset['assetId']}: scaleMeters must be [x,y,z]")


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def main() -> int:
    MANIFESTS.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    index = {"library": "spatail-starter", "rootUrl": cat.LIBRARY_ROOT_URL,
             "categories": {}, "assets": [], "total": 0}
    seen_ids = set()

    for category, block in cat.CATEGORIES.items():
        (LIB_DIR / category).mkdir(parents=True, exist_ok=True)
        (LIB_DIR / category / ".gitkeep").write_text("", encoding="utf-8")
        defaults = block["defaults"]
        assets = []
        for aid, name, tags, overrides in block["assets"]:
            if aid in seen_ids:
                problems.append(f"duplicate assetId {aid!r}")
            seen_ids.add(aid)
            meta = _asset_metadata(category, defaults, aid, name, tags, overrides)
            _validate(meta, problems)
            assets.append(meta)
        _write(MANIFESTS / f"{category}.json",
               {"category": category, "count": len(assets), "assets": assets})
        index["categories"][category] = len(assets)
        index["assets"] += [{"assetId": a["assetId"], "category": category,
                             "semanticTags": a["semanticTags"], "path": a["path"],
                             "fallbackPrimitive": a["fallbackPrimitive"],
                             "representationUses": a["representationUses"]} for a in assets]

    index["total"] = len(index["assets"])

    strategies = [{"id": s[0], "name": s[1], "engineStrategy": s[2],
                   "isNewToEngine": s[3], "rationale": s[4]} for s in cat.STRATEGIES]
    behaviors = [{"id": b[0], "name": b[1], "interactionType": b[2], "runtime": b[3]}
                 for b in cat.BEHAVIORS]

    _write(MANIFESTS / "library.json", index)
    _write(MANIFESTS / "strategies.json", {"count": len(strategies), "strategies": strategies})
    _write(MANIFESTS / "behaviors.json", {"count": len(behaviors), "behaviors": behaviors})
    _write(MANIFESTS / "schema.json", _schema_doc())

    print(f"[library] wrote {index['total']} assets across {len(index['categories'])} categories "
          f"-> {LIB_DIR}")
    for c, n in index["categories"].items():
        print(f"    {c:22} {n}")
    print(f"[library] {len(strategies)} strategies, {len(behaviors)} behaviors")
    if problems:
        print(f"[library] {len(problems)} VALIDATION PROBLEM(S):")
        for p in problems[:40]:
            print("    -", p)
        return 1
    print("[library] OK — all assets valid.")
    return 0


def _schema_doc() -> dict:
    return {
        "title": "SPATAIL library asset metadata",
        "fields": {
            "assetId": "stable unique id (snake_case)",
            "name": "human-readable name",
            "category": "library folder / domain",
            "semanticTags": "match terms for find_asset (lowercase)",
            "path": "URL of the GLB within the public/ web root (may not exist yet)",
            "scaleMeters": "[x,y,z] display size in metres (runtime rescales to room)",
            "pivot": "center | center_bottom | …",
            "placementTypes": "where it can anchor: table | floor | floating",
            "representationUses": "strategy ids (manifests/strategies.json) this asset suits",
            "supportsHighlight": "can take a highlight outline/material",
            "supportsTransparency": "can render transparent (cutaway/xray/ghost)",
            "supportsAnimation": "can be animated by a behaviour",
            "fallbackPrimitive": "runtime proxy shape when no GLB exists: "
                                 + ", ".join(sorted(FALLBACK_PRIMITIVES)),
            "qualityLevel": "starter | refined | hero",
            "license": "internal_generated | …",
            "assetState": "metadata (no GLB yet) | generated | cached",
            "primitiveSpec": "(optional) extra dims/axis for the fallback primitive",
        },
        "resolutionOrder": [
            "1 exact library asset (real GLB)",
            "2 close semantic library asset (real GLB)",
            "3 runtime primitive enriched by the matched catalog entry",
            "4 placeholder (shown immediately)",
            "5 generate in Blender -> cache back into the library",
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
