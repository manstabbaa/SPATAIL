"""job_entry.py — the live-spine seam for the Representation Engine.

A single function the always-on job server (or the educator) can call to run the
whole brain → factory → runtime → progressive-loader chain and drop the artifacts
where the server's /artifacts route can serve them. Its signature mirrors
studio/server/director.generate_experience so wiring it into job_server.py's
worker is a tiny additive branch.

Defaults to dry-run so the spine stays fast and non-blocking — the artifacts the
client needs first (Experience Plan, Asset Request Manifest, Runtime Scene
Contract, Progressive Load Plan) are all produced without Blender. A later
upgrade can flip dry_run off to stream real GLBs in behind the placeholder.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .engine import RepresentationEngine
from asset_factory.blender_factory import BlenderAssetFactory   # noqa: E402
from runtime.interaction_orchestrator import InteractionOrchestrator  # noqa: E402
from runtime.progressive_loader import ProgressiveLoader        # noqa: E402
from runtime.scene_builder import RuntimeSceneBuilder           # noqa: E402


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_library():
    """Best-effort: attach the starter Asset Library (tiers 1-4) if it's been built.
    Returns None — the factory falls back to generate — if it's absent."""
    try:
        from library.asset_library import AssetLibrary
        lib = AssetLibrary()
        return lib if lib.count else None
    except Exception:
        return None


def run_job(prompt: str, exp_id: str, out_dir, *,
            on_stage=lambda s: None, dry_run: bool = True,
            blender_exe: str | None = None) -> dict:
    """prompt → artifacts on disk. Returns a director-shaped result dict."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    on_stage("understanding")
    eng = RepresentationEngine()
    plan, manifest = eng.run(prompt)

    on_stage("factory")
    factory = BlenderAssetFactory(blender_exe=blender_exe, library=_load_library())
    pkg = factory.produce(manifest, subject=plan.subject, dry_run=dry_run,
                          placement=asdict(plan.placement), domain=plan.domain)

    on_stage("staging")
    contract = RuntimeSceneBuilder().build(plan, pkg, scene_id=exp_id, prompt=prompt)

    loader = ProgressiveLoader()
    load_plan = loader.plan_load(plan, contract, pkg)
    placeholder = loader.placeholder_scene(plan, contract)
    orch = InteractionOrchestrator()

    names = {
        "plan": f"{exp_id}.plan.json",
        "runtime": f"{exp_id}.runtime.json",
        "delivery": f"{exp_id}.delivery.json",
        "progressive": f"{exp_id}.progressive.json",
    }
    _write(out_dir / names["plan"], {"experiencePlan": plan.to_dict(),
                                     "assetRequestManifest": manifest.to_dict()})
    _write(out_dir / names["runtime"], contract)
    _write(out_dir / names["delivery"], pkg.to_dict())
    _write(out_dir / names["progressive"], {
        "loadPlan": load_plan.to_dict(),
        "placeholderScene": placeholder.to_dict(),
        "interactionGating": orch.gate(load_plan, contract),
        "interactionTriggers": orch.triggers(contract),
    })

    on_stage("ready")
    return {
        "experience_name": exp_id,                 # marks this as a representation job
        "plan_name": names["plan"],
        "runtime_name": names["runtime"],
        "delivery_name": names["delivery"],
        "progressive_name": names["progressive"],
        "title": contract["title"],
        "stations": len(pkg.assets),
        "domain": plan.domain, "intent": plan.intent, "strategy": plan.strategy,
        "builds_attempted": factory.builds_attempted,
    }
