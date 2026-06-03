"""engine.py — RepresentationEngine: the brain's public facade.

The single entry point the rest of SPATAIL talks to. It answers the question
"what is the best way for a human to understand this concept spatially?" and
hands back the two artifacts the pipeline needs:

    plan_only(prompt) -> ExperiencePlan            (enough for progressive phase-0)
    run(prompt)       -> (ExperiencePlan, AssetRequestManifest)
"""
from __future__ import annotations

from .experience_planner import ExperiencePlanner
from .manifest_builder import build_manifest
from .types import AssetRequestManifest, ExperiencePlan


class RepresentationEngine:
    def __init__(self, use_llm: bool = False):
        self.use_llm = use_llm
        self._planner = ExperiencePlanner(use_llm=use_llm)

    def plan_only(self, prompt: str, *, experience_id: str | None = None) -> ExperiencePlan:
        """The fast path: classify + plan with no Blender and no LLM (unless
        use_llm). Sufficient to render the <1 s placeholder scene."""
        return self._planner.plan(prompt, experience_id=experience_id)

    def run(self, prompt: str, *, experience_id: str | None = None,
            strategy_override: str | None = None) -> tuple[ExperiencePlan, AssetRequestManifest]:
        plan = self._planner.plan(prompt, experience_id=experience_id,
                                  strategy_override=strategy_override)
        manifest = build_manifest(plan)
        return plan, manifest
