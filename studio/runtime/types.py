"""types.py — runtime layer dataclasses.

The Runtime Scene Contract itself is built as a plain dict (shape-compatible with
contract.py's StudioSceneContract family), so only the auxiliary structures need
dataclasses here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class PlacedAsset:
    assetId: str
    role: str
    position_yup: list                  # [x, y, z] metres, glTF/RealityKit Y-up
    yaw_rad: float
    footprint_m: dict                   # {w, d, h} metres
    in_comfort_cone: bool
    status: str                         # processed | placeholder
    path: str = ""


@dataclass
class PlaceholderScene:
    """Phase-0: derivable from the Experience Plan ALONE (no Blender, no LLM)."""
    anchor: str
    title: str
    firstBeat: str
    placeholders: list = field(default_factory=list)
    needsBlender: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LoadPhase:
    phase: int
    label: str
    blocking: bool
    needsBlender: bool
    assets: list = field(default_factory=list)
    unlocks: list = field(default_factory=list)


@dataclass
class ProgressiveLoadPlan:
    firstInteractiveMs_budget: int = 1000
    phases: list = field(default_factory=list)      # list[LoadPhase]

    def to_dict(self) -> dict:
        return asdict(self)
