"""manifest_builder.py — ExperiencePlan → AssetRequestManifest (the A→B contract).

Lowers each RequiredAsset in the plan into an AssetRequest the Blender Asset
Factory can act on: a Blender-authorable description, a semantic role, the
variants the strategy needs, and the animations targeting that asset. Export
requirements always ask for modular GLB exports with metadata (bounding boxes,
pivots, named parts), per the performance requirements.
"""
from __future__ import annotations

from .types import (AssetRequest, AssetRequestManifest, ExperiencePlan,
                    ExportRequirements)

# asset.type → the manifest's semanticRole vocabulary.
_ROLE = {
    "hero": "core_structure",
    "part": "component",
    "comparison_item": "comparison_item",
    "environment": "context_environment",
    "label_target": "label_target",
}

# strategy → the variants the factory should export for the hero/parts.
_VARIANTS = {
    "exploded_view": ["complete", "exploded"],
    "cutaway_view": ["complete", "cutaway", "transparent"],
    "layered_breakdown": ["complete", "layered"],
    "assembly_sequence": ["complete", "staged"],
    "comparison_layout": ["default"],
    "animated_simulation": ["default"],
    "real_scale_placement": ["default"],
}


def _describe(plan: ExperiencePlan, asset) -> str:
    subj = plan.subject
    if asset.type == "hero":
        return f"complete {subj}, modelled with educational detail and named parts"
    if asset.type == "comparison_item":
        return f"a {subj} unit, sized for side-by-side comparison"
    if asset.type == "environment":
        return f"contextual environment / staging for the {subj}"
    return f"the {asset.id.replace('_', ' ')} component of the {subj}"


def build_manifest(plan: ExperiencePlan) -> AssetRequestManifest:
    base_variants = _VARIANTS.get(plan.strategy, ["default"])
    requests = []
    for asset in plan.requiredAssets:
        variants = list(base_variants)
        if asset.type == "part":
            # parts can be picked out individually, so always offer a highlightable cut.
            variants = sorted(set(variants + ["highlightable"]))
        motions = [a.motion for a in plan.requiredAnimations if a.targetAsset == asset.id]
        requests.append(AssetRequest(
            assetId=asset.id,
            description=_describe(plan, asset),
            semanticRole=_ROLE.get(asset.type, "component"),
            requiredVariants=variants,
            requiredAnimations=sorted(set(motions)),
        ))
    return AssetRequestManifest(
        experienceId=plan.experienceId,
        subject=plan.subject,
        strategy=plan.strategy,
        assetRequests=requests,
        exportRequirements=ExportRequirements(),
    )
