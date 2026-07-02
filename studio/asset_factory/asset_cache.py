"""asset_cache.py — content-addressed skip-if-exists cache (NET NEW).

The build driver always overwrites; nothing skips regeneration today. This cache
fills that gap: a content hash of an AssetRequest+plan maps to the GLB + registry
+ animation library it produced, persisted to studio/out/assets/asset_cache.json.
A second identical request returns the cached artifact and never invokes Blender.

Mirrors the design of engineexplainer/.../asset_library.py (a dataclass + a JSON
manifest + register/lookup) but is studio-local: it does not touch the
engineexplainer product registry.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]              # C:\SPATAIL_MAX
DEFAULT_ROOT = REPO_ROOT / "studio" / "out" / "assets"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "asset"


@dataclass
class CachedAsset:
    asset_id: str
    kind: str
    content_key: str
    glb_path: str
    registry_path: str
    anim_path: str
    bbox_m: dict = field(default_factory=dict)
    n_parts: int = 0
    created_at: str = ""


class AssetCache:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(root) if root else DEFAULT_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "asset_cache.json"
        self._index = self._load()

    def _load(self) -> dict:
        if self.manifest_path.exists():
            try:
                return json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                return {}
        return {}

    def paths_for(self, asset_id: str) -> dict:
        a = _slug(asset_id)
        return {
            "glb_path": str(self.root / f"{a}.glb"),
            "registry_path": str(self.root / f"{a}_part_registry.json"),
            "anim_path": str(self.root / f"{a}_animation_library.json"),
            "result_path": str(self.root / f"{a}.build_result.json"),
        }

    def lookup(self, content_key: str) -> CachedAsset | None:
        rec = self._index.get(content_key)
        if not rec:
            return None
        # A deleted artifact invalidates the entry — force a rebuild.
        if not os.path.exists(rec.get("glb_path", "")):
            return None
        fields = CachedAsset.__dataclass_fields__
        return CachedAsset(**{k: rec.get(k) for k in fields})

    def register(self, *, content_key: str, asset_id: str, kind: str,
                 glb_path: str, registry_path: str, anim_path: str,
                 bbox_m: dict, n_parts: int) -> CachedAsset:
        rec = CachedAsset(
            asset_id=asset_id, kind=kind, content_key=content_key,
            glb_path=glb_path, registry_path=registry_path, anim_path=anim_path,
            bbox_m=bbox_m or {}, n_parts=n_parts, created_at=_now())
        self._index[content_key] = asdict(rec)
        self._persist()
        return rec

    def _persist(self) -> None:
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._index, indent=2), encoding="utf-8")
        tmp.replace(self.manifest_path)
