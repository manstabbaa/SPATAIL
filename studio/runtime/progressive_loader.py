"""progressive_loader.py — show something within ~1 s, then stream the rest.

placeholder_scene() is PHASE-0: anchor + primitive placeholder(s) + title + the
first beat's narration, derivable from the Experience Plan ALONE — no Blender, no
LLM, no Asset Delivery Package. plan_load() then schedules the streaming phases
(swap each placeholder for its real asset, in priority order) and a final phase
that unlocks animations + interactions once their assets are present.
"""
from __future__ import annotations

import xr_design as xr

from .types import LoadPhase, PlaceholderScene, ProgressiveLoadPlan


class ProgressiveLoader:
    def stage_distance(self, plan) -> float:
        anchor = plan.placement.anchor
        if anchor == "table":
            return xr.FOCAL_PLANE_M
        if anchor == "free":
            return xr.READ_FAR_M
        return xr.clamp_distance(2.5)

    def _b2yup(self, p) -> list:
        x, y, z = p
        return [round(x, 4), round(z, 4), round(-y, 4)]

    def placeholder_scene(self, plan, contract: dict | None = None) -> PlaceholderScene:
        """Phase-0 scene. Works from `plan` alone; if a contract is supplied its
        already-computed positions are reused for pixel-perfect agreement."""
        d = self.stage_distance(plan)
        first_assets = [a for a in plan.requiredAssets if a.priority == 0] \
            or plan.requiredAssets[:1]

        positions_by_id = {}
        if contract:
            for a in contract.get("assets", []):
                positions_by_id[a.get("id")] = a.get("position_yup")

        n = len(first_assets)
        if not positions_by_id:
            arc = ([xr.focal_point(d)] if n <= 1
                   else xr.arc_positions(n, d, spread_deg=xr.OCPA_CONE_DEG))

        placeholders = []
        for i, a in enumerate(first_assets):
            pos = positions_by_id.get(a.id)
            if pos is None:
                pos = self._b2yup(arc[i] if i < len(arc) else xr.focal_point(d))
            placeholders.append({
                "assetId": a.id, "primitive": "box",
                "position_yup": pos, "footprint_m": {"w": 0.3, "h": 0.3, "d": 0.3},
                "label": a.id.replace("_", " "),
            })

        first_beat = plan.experienceBeats[0].narration if plan.experienceBeats else ""
        return PlaceholderScene(
            anchor=plan.placement.anchor,
            title=(plan.subject or "Experience").strip().title(),
            firstBeat=first_beat,
            placeholders=placeholders,
            needsBlender=False,
        )

    def plan_load(self, plan, contract: dict | None, pkg) -> ProgressiveLoadPlan:
        phases: list[LoadPhase] = []
        priority_by_id = {a.id: a.priority for a in plan.requiredAssets}
        first_ids = [a.id for a in plan.requiredAssets if a.priority == 0] \
            or [a.id for a in plan.requiredAssets[:1]]

        # Phase 0 — instant, no Blender: anchor + placeholder(s) + title + beat 1.
        phases.append(LoadPhase(
            phase=0, label="anchor + placeholder + title + first beat",
            blocking=True, needsBlender=False, assets=list(first_ids),
            unlocks=["anchor", "placeholder", "title", "first_beat"]))

        # Streaming phases — one per asset, in priority order. A cached/built
        # asset is already on disk (no Blender); a dry-run asset still needs one.
        status_by_id = {a.assetId: a.status for a in (pkg.assets if pkg else [])}
        ordered = sorted(
            (a.assetId for a in (pkg.assets if pkg else [])),
            key=lambda aid: (priority_by_id.get(aid, 9), aid))
        for k, aid in enumerate(ordered, start=1):
            pending = status_by_id.get(aid) == "dry_run"
            phases.append(LoadPhase(
                phase=k, label=f"stream {aid}", blocking=False,
                needsBlender=pending, assets=[aid],
                unlocks=[f"swap_placeholder:{aid}"]))

        # Final phase — unlock animations + interactions (runtime-driven).
        anim_ids = [a.animationId for a in (pkg.animations if pkg else [])]
        inter_ids = [it.id for it in plan.interactions]
        phases.append(LoadPhase(
            phase=len(phases), label="unlock animations + interactions",
            blocking=False, needsBlender=False, assets=[],
            unlocks=[f"animation:{a}" for a in anim_ids]
                    + [f"interaction:{i}" for i in inter_ids]))

        return ProgressiveLoadPlan(firstInteractiveMs_budget=1000, phases=phases)
