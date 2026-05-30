"""Asset library + matcher for usermanualXR.

A manual comes in as text/PDF. We classify the product it describes, then
match that classification against the SPATAIL library of available 3D
models. This module is the registry of what we CAN show, plus the matcher
that picks the best one.

For now the library is hand-registered (fan, engine) and the match is a
keyword/kind score. Swap in embeddings later without changing callers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE_DIR = HERE.parent / "engine"

# Generated (manual->XR) assets are persisted here so they re-register on a
# server restart — the in-memory ASSET_LIBRARY is rebuilt from this manifest
# at import time. Curated assets (fan/engine/shelving) stay hand-registered.
GENERATED_MANIFEST = ENGINE_DIR / "generated_assets.json"


@dataclass
class LibraryAsset:
    asset_id: str                 # canonical id used everywhere (matches web ASSETS)
    kind: str                     # human product kind
    keywords: list[str]           # match terms (lowercased)
    glb: str                      # path relative to engine/ served by the web runtime
    registry: str                 # part registry filename in engine/
    animation_library: str        # animation library filename in engine/
    blend: str | None = None      # authoring .blend (for bakes), abs path
    notes: str = ""


# --- The registered library ---------------------------------------------------

ASSET_LIBRARY: dict[str, LibraryAsset] = {
    "fan": LibraryAsset(
        asset_id="fan",
        kind="axial cooling fan",
        keywords=[
            "fan", "cooling fan", "axial fan", "case fan", "blower",
            "impeller", "blade", "rotor", "ventilation", "airflow",
            "60mm fan", "80mm fan", "120mm fan", "pc fan", "computer fan",
        ],
        glb="../engine/fan.glb",
        registry="fan_part_registry.json",
        animation_library="fan_animation_library.json",
        blend=r"C:/SPATAIL_MAX/assets_authoring/fan.blend",
        notes="60x60x10mm 7-blade axial cooling fan. Rotor spins around Y airflow axis.",
    ),
    "engine": LibraryAsset(
        asset_id="engine",
        kind="V8 internal combustion engine",
        keywords=[
            "engine", "v8", "motor", "combustion", "piston", "crankshaft",
            "cylinder", "camshaft", "block", "internal combustion", "ice",
            "powertrain", "valvetrain", "spark plug",
        ],
        glb="../engine/v8_engine.glb",
        registry="part_registry.json",
        animation_library="animation_library.json",
        blend=None,
        notes="750x600x510mm V8 ICE. 8 pistons reciprocate, crankshaft rotates.",
    ),
    # Generated asset — built from primitives by spatail_model_from_primitives
    # when no curated model matched a flat-pack manual. Proof of the generative
    # path: a manual with no library hit becomes a buildable, registered asset.
    "shelving": LibraryAsset(
        asset_id="shelving",
        kind="shelving unit",
        keywords=[
            "shelf", "shelves", "shelving", "shelving unit", "bookcase",
            "bookshelf", "rack", "cabinet", "cubby", "flat-pack", "flatpack",
            "side panel", "back panel", "cam-lock", "furniture", "assembly",
        ],
        glb="../engine/shelving_unit.glb",
        registry="shelving_unit_part_registry.json",
        animation_library="shelving_unit_animation_library.json",
        blend=None,
        notes="Generated 80x30x90cm 3-tier open shelving unit (7 flat panels). "
              "Static assembly walkthrough — no driven motion.",
    ),
}


@dataclass
class MatchResult:
    asset: LibraryAsset | None
    score: float
    reason: str
    alternatives: list[tuple[str, float]] = field(default_factory=list)


def match_product(product_kind: str, keywords: list[str] | None = None) -> MatchResult:
    """Score every library asset against the manual's classified product.

    product_kind: the ingest agent's one-line product classification
                  (e.g. "axial cooling fan", "lawn mower engine").
    keywords:     extra terms the ingest agent pulled from the manual.

    Returns the best LibraryAsset (or None if nothing clears the floor),
    its score, a human reason, and the ranked alternatives.
    """
    text = " ".join([product_kind or ""] + (keywords or [])).lower()
    scores: list[tuple[str, float, int]] = []
    for aid, asset in ASSET_LIBRARY.items():
        hits = 0
        # kind substring is a strong signal
        kind_hit = 2 if asset.kind.lower() in text or any(
            w in text for w in asset.kind.lower().split()) else 0
        for kw in asset.keywords:
            if kw in text:
                hits += 1
        score = kind_hit + hits
        scores.append((aid, float(score), hits))

    scores.sort(key=lambda t: -t[1])
    best_id, best_score, best_hits = scores[0]
    alternatives = [(aid, s) for aid, s, _ in scores[1:]]

    # Floor: need at least 1 keyword hit to claim a match
    if best_score < 1:
        return MatchResult(asset=None, score=best_score,
                           reason=f"No library asset matched '{product_kind}'. "
                                  f"Available kinds: {[a.kind for a in ASSET_LIBRARY.values()]}",
                           alternatives=alternatives)

    asset = ASSET_LIBRARY[best_id]
    return MatchResult(
        asset=asset, score=best_score,
        reason=f"Matched '{product_kind}' → '{asset.kind}' "
               f"({best_hits} keyword hits, score {best_score:.0f}).",
        alternatives=alternatives,
    )


def registry_path(asset: LibraryAsset) -> Path:
    return ENGINE_DIR / asset.registry


def animation_library_path(asset: LibraryAsset) -> Path:
    return ENGINE_DIR / asset.animation_library


# --- Generated-asset registration (the manual->XR generative path) -----------

def _load_generated_manifest() -> dict:
    if not GENERATED_MANIFEST.exists():
        return {}
    try:
        return json.loads(GENERATED_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _persist_generated(asset: LibraryAsset) -> None:
    data = _load_generated_manifest()
    data[asset.asset_id] = {
        "asset_id": asset.asset_id, "kind": asset.kind,
        "keywords": asset.keywords, "glb": asset.glb,
        "registry": asset.registry, "animation_library": asset.animation_library,
        "notes": asset.notes, "_generated": True,
    }
    GENERATED_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    GENERATED_MANIFEST.write_text(json.dumps(data, indent=2), encoding="utf-8")


def register_generated_asset(asset_id: str, kind: str, keywords: list[str],
                             glb: str, registry: str, animation_library: str,
                             notes: str = "", persist: bool = True) -> LibraryAsset:
    """Register a generated asset as a first-class library asset.

    Adds it to the in-memory ASSET_LIBRARY (so match_product can find it
    immediately, no restart needed) and persists it to the manifest (so it
    survives a restart of the intelligence server)."""
    asset = LibraryAsset(
        asset_id=asset_id, kind=kind,
        keywords=[k.lower() for k in keywords if k],
        glb=glb, registry=registry, animation_library=animation_library,
        blend=None, notes=notes,
    )
    ASSET_LIBRARY[asset_id] = asset
    if persist:
        _persist_generated(asset)
    return asset


def is_generated(asset_id: str) -> bool:
    return asset_id in _load_generated_manifest()


# Re-register persisted generated assets at import (survives server restart).
for _aid, _rec in _load_generated_manifest().items():
    if _aid not in ASSET_LIBRARY:
        try:
            ASSET_LIBRARY[_aid] = LibraryAsset(
                asset_id=_rec["asset_id"], kind=_rec.get("kind", ""),
                keywords=_rec.get("keywords", []), glb=_rec["glb"],
                registry=_rec["registry"],
                animation_library=_rec["animation_library"],
                blend=None, notes=_rec.get("notes", ""),
            )
        except Exception:
            pass
