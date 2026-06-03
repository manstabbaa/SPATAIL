"""experience_plan.py — build an EducationalExperiencePlan for a selection.

Reuses the existing pipeline end to end — Representation Engine → Asset Factory
(library-first) → Runtime Scene Builder → Progressive Loader — and wraps the result
with the educational fields from the task spec (mode, selectedText, learningGoal,
targetAudience, preferredRepresentation, beats, interactions). It does NOT build AR
scenes or duplicate any runtime logic; it produces the same RuntimeSceneContract the
iOS RepresentationRuntime already renders, plus an `educational` block.
"""
from __future__ import annotations

import re
from dataclasses import asdict

from . import reader
from .prompt_builder import EducationalPromptBuilder
from representation.engine import RepresentationEngine          # noqa: E402
from representation.types import ExperienceBeat                 # noqa: E402
from asset_factory.blender_factory import BlenderAssetFactory   # noqa: E402
from runtime.interaction_orchestrator import InteractionOrchestrator  # noqa: E402
from runtime.progressive_loader import ProgressiveLoader        # noqa: E402
from runtime.scene_builder import RuntimeSceneBuilder           # noqa: E402

_WORD = re.compile(r"[^a-z0-9]+")


def _slug(s: str) -> str:
    return _WORD.sub("_", (s or "").lower()).strip("_") or "beat"


def _load_library():
    try:
        from library.asset_library import AssetLibrary
        lib = AssetLibrary()
        return lib if lib.count else None
    except Exception:
        return None


def build_educational_experience(selected_text: str, surrounding_context: str = "",
                                 learning_goal: str | None = None,
                                 audience: str = "general learner", *,
                                 experience_id: str | None = None,
                                 beats=None, interactions=None, library=None,
                                 factory=None) -> dict:
    built = EducationalPromptBuilder().build(selected_text, surrounding_context,
                                             learning_goal, audience)
    concept = reader.BY_ID.get(built["conceptId"]) if built["conceptId"] else None
    beats = beats or (concept or {}).get("experienceBeats")
    edu_interactions = interactions or (concept or {}).get("interactions")

    # 1) classify + plan via the existing engine (clean prompt + the reader's strategy)
    eng = RepresentationEngine()
    plan, manifest = eng.run(built["enginePrompt"], experience_id=experience_id,
                             strategy_override=built["engineStrategy"])

    # 2) enrich the plan with the concept's specific beats (better than generic templates)
    if beats:
        hero = plan.requiredAssets[0].id if plan.requiredAssets else _slug(plan.subject)
        plan.experienceBeats = [
            ExperienceBeat(id=(_slug(b)[:40] or f"beat_{i}"), title=b, narration=b,
                           focus=hero, reveal=("auto" if i == 0 else "on_tap"))
            for i, b in enumerate(beats)]

    # 3) factory (library-first, no Blender) + runtime contract + progressive plan
    if factory is None:
        factory = BlenderAssetFactory(library=library if library is not None else _load_library())
    pkg = factory.produce(manifest, subject=plan.subject, dry_run=True,
                          placement=asdict(plan.placement), domain=plan.domain)
    contract = RuntimeSceneBuilder().build(plan, pkg, scene_id=plan.experienceId,
                                           prompt=built["spatailPrompt"])
    loader = ProgressiveLoader()
    load = loader.plan_load(plan, contract, pkg)
    placeholder = loader.placeholder_scene(plan, contract)
    orch = InteractionOrchestrator()

    # 4) tag the runtime contract as educational (additive keys; runtime ignores unknown)
    contract["mode"] = "educational_wrapper"
    contract["educational"] = {
        "selectedText": selected_text, "surroundingContext": surrounding_context,
        "learningGoal": built["learningGoal"], "targetAudience": audience,
        "preferredRepresentation": built["preferredRepresentation"],
        "conceptId": built["conceptId"], "spatailPrompt": built["spatailPrompt"],
    }

    return {
        "mode": "educational_wrapper",
        "selectedText": selected_text,
        "surroundingContext": surrounding_context,
        "learningGoal": built["learningGoal"],
        "targetAudience": audience,
        "preferredRepresentation": built["preferredRepresentation"],
        "conceptId": built["conceptId"],
        "spatailPrompt": built["spatailPrompt"],
        "experienceBeats": [b.title for b in plan.experienceBeats],
        "interactions": edu_interactions or [i.id for i in plan.interactions],
        "experiencePlan": plan.to_dict(),
        "assetRequestManifest": manifest.to_dict(),
        "runtimeContract": contract,
        "deliveryPackage": pkg.to_dict(),
        "progressive": {
            "loadPlan": load.to_dict(),
            "placeholderScene": placeholder.to_dict(),
            "interactionGating": orch.gate(load, contract),
            "interactionTriggers": orch.triggers(contract),
        },
    }
