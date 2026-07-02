"""types.py — the two shared, JSON-serialisable contracts the brain emits.

  ExperiencePlan        — the decision: how to present a concept spatially.
  AssetRequestManifest  — what the Blender Asset Factory must produce for it.

Stdlib only and dependency-free (no xr_design, no Blender) so the planner and
the <1 s progressive phase-0 can import it without pulling anything heavy. Field
names follow the SPATAIL task spec verbatim; `placement` and `interactions[].type`
values are restricted to the studio Experience Spec vocab (experience_spec.py) so
a plan can later be lowered into a valid es.Experience.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ExperienceBeat:
    """One narrated moment in the experience. `focus` names the asset id / role
    the camera and attention center on; `reveal` is the trigger to advance."""
    id: str
    title: str
    narration: str
    focus: str = ""               # assetId or semantic role this beat centers on
    reveal: str = "auto"          # on_focus | on_tap | auto


@dataclass
class RequiredAsset:
    id: str
    type: str                     # hero | part | comparison_item | environment | label_target
    priority: int = 0             # 0 = needed for the first beat after the placeholder


@dataclass
class RequiredAnimation:
    id: str
    targetAsset: str
    motion: str                   # reciprocate | orbit | spin | assemble
    note: str = ""


@dataclass
class InteractionSpec:
    id: str
    type: str                     # MUST be in experience_spec.MECHANIC_TYPES
    label: str = ""
    params: dict = field(default_factory=dict)


@dataclass
class Placement:
    anchor: str = "table"         # experience_spec.ANCHORS  {table, floor, free}
    layout: str = "arc"           # experience_spec.LAYOUTS  {arc, line, cluster}
    facing: str = "user"          # experience_spec.FACINGS  {user, forward}
    scale_mode: str = "dynamic"   # experience_spec.SCALE_MODES {dynamic, fixed}


@dataclass
class ExperiencePlan:
    """The brain's output: the spatial-communication strategy for a prompt."""
    experienceId: str
    domain: str
    intent: str
    strategy: str
    subject: str
    placement: Placement = field(default_factory=Placement)
    domainConfidence: float = 0.0
    intentConfidence: float = 0.0
    source: str = "rule"          # rule | llm | rule+llm
    reasoning: str = ""           # why this strategy (for the contract / UI)
    experienceBeats: list[ExperienceBeat] = field(default_factory=list)
    requiredAssets: list[RequiredAsset] = field(default_factory=list)
    requiredAnimations: list[RequiredAnimation] = field(default_factory=list)
    interactions: list[InteractionSpec] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExportRequirements:
    format: str = "glb"
    modularExports: bool = True
    includeMetadata: bool = True
    includeBoundingBoxes: bool = True
    includePivots: bool = True
    includeNamedParts: bool = True


@dataclass
class AssetRequest:
    assetId: str
    description: str               # a Blender-authorable description of the asset
    semanticRole: str
    requiredVariants: list[str] = field(default_factory=list)
    requiredAnimations: list[str] = field(default_factory=list)


@dataclass
class AssetRequestManifest:
    """The A→B contract: handed to the Blender Asset Factory."""
    experienceId: str
    subject: str
    strategy: str
    assetRequests: list[AssetRequest] = field(default_factory=list)
    exportRequirements: ExportRequirements = field(default_factory=ExportRequirements)

    def to_dict(self) -> dict:
        return asdict(self)
