"""asset_library.py — the registry + 5-tier resolver over the starter library.

Loads the JSON manifests built by build_library.py and answers:
  find_asset(domain, semantic_tags, representation_use) -> AssetMatch
  resolve(asset_id, subject, domain, semantic_role, strategy) -> Resolution

Resolution tiers (NO placeholders — every miss becomes real geometry):
  library    — an exact/close catalog asset that has a real GLB (assetState generated/cached)
  primitive  — best semantic catalog match; use its fallbackPrimitive + scale + pivot
  generate   — nothing fit; hand off to the Blender factory (which then register_generated)

Mirrors the proven engineexplainer/.../asset_library.py pattern (keyword scorer +
manifest reload), studio-local and richer (full metadata + tiers + generated bucket).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO_ROOT / "public" / "assets" / "spatail-library"

# Engine domain (taxonomy.DOMAINS) -> preferred library categories (boosted in scoring).
DOMAIN_TO_CATEGORIES = {
    "mechanical": ["mechanical"],
    "biological": ["biology"],
    "scientific": ["physics", "astronomy", "biology"],
    "architectural": ["architecture"],
    "historical": ["education", "environments"],
    "product": ["product-preview", "furniture"],
    "manufacturing": ["mechanical", "architecture"],
    "data_visualization": ["data-visualization"],
    "educational": ["education"],
    "general_knowledge": [],
}

_FLOOR = 1.5            # minimum score to claim a semantic (non-placeholder) match
_STOP = {"the", "a", "an", "of", "on", "in", "my", "show", "me", "explain", "how",
         "is", "are", "to", "and", "with", "part", "default", "complete", "set"}
# Generic words that match many assets — a hit on one counts for less, so a
# specific signal ("solar" -> sun) outweighs a generic one ("system" -> spring_mass_system).
_GENERIC = {"system", "unit", "block", "simple", "simplified", "generic", "segment",
            "panel", "object", "piece", "element", "module", "assembly", "body", "core"}
_GENERIC_WEIGHT = 0.4
_WORD = re.compile(r"[^a-z0-9]+")


def _tokens(s: str) -> list[str]:
    base = [t for t in _WORD.split((s or "").lower()) if t and t not in _STOP]
    out = list(base)
    for t in base:                                   # cheap depluralisation: tvs->tv, cells->cell
        if len(t) > 2 and t.endswith("s") and t[:-1] not in out:
            out.append(t[:-1])
    return out


@dataclass
class AssetMatch:
    asset: dict | None
    score: float
    reason: str
    alternatives: list = field(default_factory=list)   # [(assetId, score)]


@dataclass
class Resolution:
    source: str                 # library | primitive | generate
    libraryAssetId: str = ""
    glbPath: str = ""           # web path if a real GLB exists
    usdzPath: str = ""          # web path to the cached USDZ (for the iOS runtime)
    fallbackPrimitive: str = "cube"
    scaleMeters: list = field(default_factory=lambda: [0.1, 0.1, 0.1])
    pivot: str = "center"
    reason: str = ""


class AssetLibrary:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else DEFAULT_ROOT
        self.manifests = self.root / "manifests"
        self.assets: dict[str, dict] = {}       # assetId -> metadata
        self.by_category: dict[str, list[str]] = {}
        self.strategies: list[dict] = []
        self._load()

    # -- loading --------------------------------------------------------------
    def _load(self) -> None:
        self.assets.clear()
        self.by_category.clear()
        if not self.manifests.exists():
            return
        _SKIP = ("library.json", "behaviors.json", "schema.json", "generated.json")
        for mf in sorted(self.manifests.glob("*.json")):
            if mf.name in _SKIP:
                continue
            if mf.name == "strategies.json":
                try:
                    self.strategies = json.loads(mf.read_text(encoding="utf-8")).get("strategies", [])
                except (ValueError, OSError):
                    self.strategies = []
                continue
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            for a in data.get("assets", []):
                aid = a.get("assetId")
                if not aid:
                    continue
                self.assets[aid] = a
                self.by_category.setdefault(a.get("category", "?"), []).append(aid)
        # generated.json overlay LAST → baked assets OVERRIDE their catalog metadata
        # (assetState flips to "generated"/"cached" with real glb/usdz paths).
        gen = self.manifests / "generated.json"
        if gen.exists():
            try:
                for a in json.loads(gen.read_text(encoding="utf-8")).get("assets", []):
                    aid = a.get("assetId")
                    if aid:
                        self.assets[aid] = a
                        self.by_category.setdefault(a.get("category", "generated"), []).append(aid)
            except (ValueError, OSError):
                pass

    @property
    def count(self) -> int:
        return len(self.assets)

    # -- search ---------------------------------------------------------------
    def find_asset(self, *, domain: str | None = None, semantic_tags=(),
                   representation_use: str | None = None,
                   name_hint: str | None = None) -> AssetMatch:
        """Score every asset against the query; return the best + alternatives."""
        query = set()
        for t in semantic_tags or ():
            query.update(_tokens(t))
        if name_hint:
            query.update(_tokens(name_hint))
        # ranked domain-preferred category boost: the primary category outweighs
        # secondary ones, so a "tv" in the product domain picks product-preview
        # over furniture (both contain a "tv*").
        cats = DOMAIN_TO_CATEGORIES.get(domain or "", [])
        cat_boost = {c: round(max(0.2, 1.0 - 0.4 * i), 3) for i, c in enumerate(cats)}

        scored: list[tuple[str, float]] = []
        for aid, a in self.assets.items():
            if a.get("category") == "placeholders":
                continue                                  # placeholders resolve via tier 4 only
            tagset = set(a.get("semanticTags", [])) | set(_tokens(aid)) | set(_tokens(a.get("name", "")))
            overlap = query & tagset
            hits = sum(_GENERIC_WEIGHT if t in _GENERIC else 1.0 for t in overlap)
            cb = cat_boost.get(a.get("category"), 0.0)
            if not overlap and cb == 0.0:
                continue
            score = hits + cb                             # specificity-weighted hits + category boost
            if representation_use and representation_use in a.get("representationUses", []):
                score += 0.5
            # real geometry wins ties (tier 1/2 over tier 3)
            if a.get("assetState") in ("generated", "cached"):
                score += 0.25
            scored.append((aid, score))

        if not scored:
            return AssetMatch(asset=None, score=0.0,
                              reason=f"no library asset matched tags {sorted(query)} (domain={domain})")
        scored.sort(key=lambda t: -t[1])
        best_id, best_score = scored[0]
        return AssetMatch(asset=self.assets[best_id], score=best_score,
                          reason=f"matched {best_id!r} (score {best_score:.2f})",
                          alternatives=scored[1:6])

    # -- resolve (the 5-tier hierarchy) --------------------------------------
    def resolve(self, *, asset_id: str, subject: str = "", domain: str | None = None,
                semantic_role: str = "", strategy: str | None = None) -> Resolution:
        tags = list(_tokens(asset_id)) + list(_tokens(subject)) + list(_tokens(semantic_role))
        m = self.find_asset(domain=domain, semantic_tags=tags, representation_use=strategy)

        if m.asset and m.score >= _FLOOR:
            a = m.asset
            if a.get("assetState") in ("generated", "cached"):
                return Resolution(source="library", libraryAssetId=a["assetId"],
                                  glbPath=a.get("path", ""), usdzPath=a.get("usdzPath", ""),
                                  fallbackPrimitive=a.get("fallbackPrimitive", "cube"),
                                  scaleMeters=a.get("scaleMeters", [0.1, 0.1, 0.1]),
                                  pivot=a.get("pivot", "center"),
                                  reason=f"library GLB {a['assetId']} ({m.reason})")
            return Resolution(source="primitive", libraryAssetId=a["assetId"],
                              fallbackPrimitive=a.get("fallbackPrimitive", "cube"),
                              scaleMeters=a.get("scaleMeters", [0.1, 0.1, 0.1]),
                              pivot=a.get("pivot", "center"),
                              reason=f"primitive enriched by {a['assetId']} ({m.reason})")

        # NO PLACEHOLDERS: nothing in the library fit → hand off to the Blender factory
        # to MAKE it; register_generated() then feeds the result back as a library asset.
        return Resolution(source="generate",
                          reason=f"no library match (best {m.score:.2f} < {_FLOOR}) -> generate in Blender")

    # -- tier-5 feedback: a generated asset becomes a library asset -----------
    def register_generated(self, asset_id: str, glb_path: str, *,
                           category: str = "generated", name: str = "",
                           semantic_tags=(), scale_meters=None, pivot: str = "center_bottom",
                           representation_uses=(), usdz_path: str = "", bbox_m=None,
                           fallback_primitive: str = "cube",
                           placement_types=("table", "floor"), asset_state: str = "generated") -> dict:
        """Persist a freshly generated/baked asset to manifests/generated.json and index
        it, so the next resolve() returns it as a `library` (real GLB) hit. usdz_path lets
        the iOS runtime load the cached USDZ."""
        meta = {
            "assetId": asset_id, "name": name or asset_id.replace("_", " ").title(),
            "category": category, "semanticTags": list(semantic_tags) or _tokens(asset_id),
            "path": glb_path, "usdzPath": usdz_path,
            "scaleMeters": list(scale_meters or [0.3, 0.3, 0.3]),
            "pivot": pivot, "placementTypes": list(placement_types),
            "representationUses": list(representation_uses), "supportsHighlight": True,
            "supportsTransparency": True, "supportsAnimation": True,
            "fallbackPrimitive": fallback_primitive, "qualityLevel": "generated",
            "license": "internal_generated", "assetState": asset_state,
            "boundingBoxMeters": bbox_m or {},
        }
        gen_path = self.manifests / "generated.json"
        try:
            existing = json.loads(gen_path.read_text(encoding="utf-8")).get("assets", []) \
                if gen_path.exists() else []
        except (ValueError, OSError):
            existing = []
        existing = [a for a in existing if a.get("assetId") != asset_id] + [meta]
        gen_path.parent.mkdir(parents=True, exist_ok=True)
        gen_path.write_text(json.dumps({"category": "generated", "count": len(existing),
                                        "assets": existing}, indent=2), encoding="utf-8")
        self.assets[asset_id] = meta
        self.by_category.setdefault("generated", []).append(asset_id)
        return meta
