"""types.py — the B→A contract (Asset Delivery Package) and factory internals.

Field names follow the SPATAIL task spec. Bounding boxes and pivots are in the
driver's native Blender Z-up metres frame ({min,max,size,center}); the runtime
swaps them to Y-up when it lays the scene out.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class AssetMetadata:
    boundingBoxMeters: dict                 # {min,max,size,center} Blender Z-up, metres
    pivot: list                             # [x,y,z] base-centre, Z-up metres
    semanticRole: str
    recommendedPlacement: dict              # {anchor, layout} (es vocab)
    realWorldScale: list                    # size [x,y,z], metres


@dataclass
class DeliveredAsset:
    assetId: str
    path: str                               # the modular GLB (complete/default variant)
    variants: dict = field(default_factory=dict)     # variant name -> glb path
    metadata: AssetMetadata | None = None
    status: str = "dry_run"                 # dry_run | built | cached | library | primitive | placeholder
    source: str = "generate"                # which resolution tier produced it
    libraryAssetId: str = ""                # the starter-library catalog asset that resolved it
    fallbackPrimitive: str = ""             # runtime proxy shape when path is empty


@dataclass
class DeliveredAnimation:
    animationId: str
    targetAssets: list
    type: str                               # looping | assemble | reciprocate | orbit | spin
    path: str = ""                          # baked-clip glb, or "" for a runtime tween


@dataclass
class AssetDeliveryPackage:
    experienceId: str
    assets: list[DeliveredAsset] = field(default_factory=list)
    animations: list[DeliveredAnimation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BuildOutcome:
    """Internal: 1:1 with the build driver's result sidecar, plus factory flags."""
    ok: bool
    assetId: str
    glb_path: str = ""
    registry_path: str = ""
    anim_path: str = ""
    bbox_m: dict = field(default_factory=dict)
    n_parts: int = 0
    elapsed_s: float = 0.0
    dry_run: bool = False
    cached: bool = False
    error: str = ""
