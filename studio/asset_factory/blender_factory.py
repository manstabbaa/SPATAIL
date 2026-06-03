"""blender_factory.py — BlenderAssetFactory: manifest → AssetDeliveryPackage.

Per AssetRequest:
  1. derive the cm build plan (plan_adapter) and a content key;
  2. AssetCache hit  → return the cached GLB, NO Blender (status "cached");
  3. dry-run         → predict paths + bbox, full metadata, NO Blender ("dry_run");
  4. real            → subprocess the headless build driver, register in the cache
                       ("built"). Mirrors engineexplainer/.../generative_bridge
                       .build_asset_from_plan: spec→tempfile, run Blender headless,
                       read the result sidecar, raise with the stderr tail on failure.

Blender is single-threaded, so a "batch" is sequential subprocesses; the win is
cache-coalescing (repeated subjects/animations don't re-invoke Blender). Layout,
sequencing and interaction are NOT done here — that is the runtime's job.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from .asset_cache import AssetCache
from .plan_adapter import content_key, predict_bbox_from_plan, request_to_plan
from .types import (AssetDeliveryPackage, AssetMetadata, BuildOutcome,
                    DeliveredAnimation, DeliveredAsset)

REPO_ROOT = Path(__file__).resolve().parents[2]                 # C:\SPATAIL_MAX
BUILD_SCRIPT = REPO_ROOT / "pipeline" / "blender" / "spatail_build_from_plan_driver.py"
BAKE_SCRIPT = REPO_ROOT / "pipeline" / "blender" / "spatail_bake_one_animation.py"
BLENDER_EXE = os.environ.get(
    "BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")

_LOOPING = {"spin", "orbit", "reciprocate"}


class BlenderAssetFactory:
    def __init__(self, cache: AssetCache | None = None, blender_exe: str | None = None,
                 build_timeout: float = 600.0, library=None):
        self.cache = cache or AssetCache()
        self.blender_exe = blender_exe or BLENDER_EXE
        self.build_timeout = build_timeout
        self.library = library            # optional studio.library.AssetLibrary (tiers 1-4)
        self.builds_attempted = 0          # how many real Blender subprocesses ran

    # -- public ---------------------------------------------------------------
    def produce(self, manifest, *, subject: str | None = None, dry_run: bool = True,
                only=None, placement: dict | None = None,
                domain: str | None = None) -> AssetDeliveryPackage:
        """Consume an AssetRequestManifest → AssetDeliveryPackage.

        Resolution order per asset: starter-library (tiers 1-4, no Blender) →
        generate in Blender (tier 4) → cache (tier 5). dry_run=True (default) never
        invokes Blender; dry_run=False builds for real (limit to `only` ids)."""
        subject = subject or manifest.subject
        strategy = getattr(manifest, "strategy", None)
        only = set(only) if only else None
        pkg = AssetDeliveryPackage(experienceId=manifest.experienceId)
        for req in manifest.assetRequests:
            really = (not dry_run) and (only is None or req.assetId in only)
            pkg.assets.append(self._produce_one(req, subject, really, placement, domain, strategy))
        pkg.animations = self._animations(manifest)
        return pkg

    # -- per-asset ------------------------------------------------------------
    def _produce_one(self, req, subject: str, really: bool, placement: dict | None,
                     domain: str | None = None, strategy: str | None = None) -> DeliveredAsset:
        plan = request_to_plan(req, subject=subject)
        key = content_key(req, plan)

        # tier 5: already generated+cached
        hit = self.cache.lookup(key)
        if hit:
            return self._assemble(req, hit.glb_path, hit.bbox_m, "cached", placement, source="cached")

        # tier 4: explicit real build -> register back into the library (tier 5)
        if really:
            outcome = self._build(req, plan, key, subject)
            if self.library is not None:
                try:
                    self.library.register_generated(
                        outcome.assetId, outcome.glb_path, name=subject,
                        semantic_tags=[subject], scale_meters=outcome.bbox_m.get("size"),
                        representation_uses=[strategy] if strategy else [])
                except Exception:  # noqa: BLE001 — library write must never fail a build
                    pass
            return self._assemble(req, outcome.glb_path, outcome.bbox_m, "built", placement, source="built")

        # tiers 1-4: consult the starter library first (no Blender)
        if self.library is not None:
            res = self.library.resolve(asset_id=req.assetId, subject=subject, domain=domain,
                                       semantic_role=req.semanticRole, strategy=strategy)
            if res.source in ("library", "primitive", "placeholder"):
                return self._deliver_from_library(req, res, placement)

        # nothing fit: the predicted primitive plan (would generate on a real pass)
        bbox = predict_bbox_from_plan(plan)
        return self._assemble(req, self.cache.paths_for(req.assetId)["glb_path"], bbox,
                              "dry_run", placement, source="generate")

    def _deliver_from_library(self, req, res, placement: dict | None) -> DeliveredAsset:
        s = res.scaleMeters or [0.1, 0.1, 0.1]
        sx, sy, sz = float(s[0]), float(s[1]), float(s[2])
        bbox = {"min": [round(-sx / 2, 4), round(-sy / 2, 4), 0.0],
                "max": [round(sx / 2, 4), round(sy / 2, 4), round(sz, 4)],
                "size": [round(sx, 4), round(sy, 4), round(sz, 4)],
                "center": [0.0, 0.0, round(sz / 2, 4)]}
        path = res.glbPath if res.source == "library" else ""
        return self._assemble(req, path, bbox, res.source, placement, source=res.source,
                              library_id=res.libraryAssetId, fallback=res.fallbackPrimitive)

    def _assemble(self, req, glb_path: str, bbox_m: dict, status: str,
                  placement: dict | None, *, source: str = "generate",
                  library_id: str = "", fallback: str = "") -> DeliveredAsset:
        bbox_m = bbox_m or {}
        size = bbox_m.get("size", [0.0, 0.0, 0.0])
        center = bbox_m.get("center", [0.0, 0.0, 0.0])
        mn = bbox_m.get("min", [0.0, 0.0, 0.0])
        pivot = [round(center[0], 4), round(center[1], 4), round(mn[2], 4)]  # base-centre, Z-up
        pl = placement or {}
        meta = AssetMetadata(
            boundingBoxMeters=bbox_m,
            pivot=pivot,
            semanticRole=req.semanticRole,
            recommendedPlacement={"anchor": pl.get("anchor", "table"),
                                  "layout": pl.get("layout", "arc")},
            realWorldScale=[round(v, 4) for v in size],
        )
        return DeliveredAsset(assetId=req.assetId, path=glb_path,
                              variants=self._variant_paths(req, glb_path) if glb_path else {},
                              metadata=meta, status=status, source=source,
                              libraryAssetId=library_id, fallbackPrimitive=fallback)

    def _variant_paths(self, req, base_glb: str) -> dict:
        out = {}
        for v in (req.requiredVariants or ["default"]):
            if v in ("default", "complete"):
                out[v] = base_glb
            else:
                out[v] = base_glb[:-4] + f"_{v}.glb" if base_glb.endswith(".glb") else base_glb
        return out

    def _animations(self, manifest) -> list:
        seen: dict[str, DeliveredAnimation] = {}
        for req in manifest.assetRequests:
            for motion in (req.requiredAnimations or []):
                aid = f"{req.assetId}_{motion}"
                if aid in seen:
                    continue
                anim_type = "looping" if motion in _LOOPING else motion
                # No baked glTF clips: the driver emits a runtime assembly tween,
                # and looping motions are runtime-driven, so path stays "".
                seen[aid] = DeliveredAnimation(animationId=aid, targetAssets=[req.assetId],
                                               type=anim_type, path="")
        return list(seen.values())

    # -- real build (subprocess) ----------------------------------------------
    def _build(self, req, plan: dict, key: str, subject: str) -> BuildOutcome:
        self.builds_attempted += 1
        paths = self.cache.paths_for(req.assetId)
        result_path = paths["result_path"]
        try:
            os.remove(result_path)              # clear a stale sidecar
        except OSError:
            pass

        spec = {
            "assetId": plan["assetId"],
            "plan": plan,
            "glb_path": paths["glb_path"],
            "registry_path": paths["registry_path"],
            "anim_path": paths["anim_path"],
            "result_path": result_path,
        }
        fd, spec_path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(spec, f)

            if not os.path.exists(self.blender_exe):
                raise RuntimeError(
                    f"Blender not found at {self.blender_exe!r} (set BLENDER_EXE).")

            cmd = [self.blender_exe, "--background", "--python", str(BUILD_SCRIPT),
                   "--", spec_path]
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=self.build_timeout)

            if not os.path.exists(result_path):
                tail = (proc.stderr or proc.stdout or "")[-1800:]
                raise RuntimeError(
                    f"build driver wrote no result for {req.assetId!r}.\n{tail}")
            sidecar = json.loads(Path(result_path).read_text(encoding="utf-8"))
            if not sidecar.get("ok"):
                raise RuntimeError(
                    f"build failed for {req.assetId!r}: {sidecar.get('error')}\n"
                    f"{(sidecar.get('trace') or '')[:1800]}")

            outcome = BuildOutcome(
                ok=True, assetId=sidecar.get("assetId", plan["assetId"]),
                glb_path=sidecar["glb_path"], registry_path=sidecar["registry_path"],
                anim_path=sidecar["anim_path"], bbox_m=sidecar.get("bbox_m", {}),
                n_parts=sidecar.get("n_parts", 0))
            self.cache.register(
                content_key=key, asset_id=outcome.assetId, kind=subject,
                glb_path=outcome.glb_path, registry_path=outcome.registry_path,
                anim_path=outcome.anim_path, bbox_m=outcome.bbox_m,
                n_parts=outcome.n_parts)
            return outcome
        finally:
            try:
                os.remove(spec_path)
            except OSError:
                pass
