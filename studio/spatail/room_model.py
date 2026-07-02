"""room_model.py — the SPATAIL RoomModel: the placement solver's INPUT.

RoomModel is a compact, SEMANTIC description of the user's physical space — "the
room, understood." It is the input to the Placement Design System solver. It is
NOT a mesh or a scan file; it is the small structured abstraction the placement
rules reason over, so the same rules run identically on device (live) and on PC
(authoring/preview/tests).

WHERE IT COMES FROM (all on-device, accumulated over a few seconds of looking
around, then distilled — see ios .../ARContainerView.swift, which already builds
the coarse RoomProfile this evolves from):

    ARKit source                              -> RoomModel field
    ----------------------------------------- -------------------------------------
    ARPlaneAnchor (.planeExtent/.transform/   -> surfaces[]  (with .classification:
        .alignment/.classification)              floor/wall/ceiling/table/seat/...)
    ARMeshAnchor (sceneReconstruction =       -> surfaces[] (precise), obstacles[],
        .meshWithClassification, LiDAR)          occlusion geometry
    ARFrame.lightEstimate                     -> light (intensity, colorTemp)
    ARFrame.camera.transform                  -> user (position, forward, eyeHeight)
    ARFrame.sceneDepth (LiDAR)                -> obstacles / free-space refinement
    RoomPlan CapturedRoom (optional one-shot) -> a near-complete RoomModel
    Vision/CoreML object detection (optional) -> obstacles[] with object identity

Producers (ARKit / RoomPlan / CV) all WRITE this schema; the solver only ever
READS it — so producers are swappable without touching placement logic.

Frame: metres, +Y up, the user near the origin looking -Z (the AR/USDZ convention
the device uses; matches studio/spatail/analysis.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

SCHEMA = "spatail-room-model/1"

# Semantic surface classes (mirror ARKit's ARMeshClassification / plane classes).
SURFACE_CLASSES = {
    "floor", "table", "wall", "ceiling", "seat", "shelf", "counter", "unknown",
}


@dataclass
class Surface:
    """One semantic planar surface the user could place content on/near."""
    cls: str                              # one of SURFACE_CLASSES
    center_m: tuple = (0.0, 0.0, 0.0)     # world centre (x, y, z)
    size_m: tuple = (1.0, 1.0)            # extent along the surface's local (u, v)
    normal: tuple = (0.0, 1.0, 0.0)       # surface normal (up for floor/table)
    yaw_rad: float = 0.0                  # rotation of the extent rectangle about normal
    height_m: float = 0.0                 # height above floor (table≈0.74, floor=0)
    confidence: float = 1.0

    def as_dict(self):
        d = asdict(self)
        for k in ("center_m", "size_m", "normal"):
            d[k] = list(getattr(self, k))
        return d


@dataclass
class Obstacle:
    """A volume to keep content out of: furniture, clutter, a detected object."""
    bbox_min_m: tuple = (0.0, 0.0, 0.0)
    bbox_max_m: tuple = (0.0, 0.0, 0.0)
    category: str = "unknown"            # "table","chair","lamp",... (RoomPlan/CV)
    confidence: float = 1.0

    def as_dict(self):
        d = asdict(self)
        d["bbox_min_m"] = list(self.bbox_min_m)
        d["bbox_max_m"] = list(self.bbox_max_m)
        return d


@dataclass
class UserPose:
    position_m: tuple = (0.0, 0.0, 0.0)  # where the user stands
    forward: tuple = (0.0, 0.0, -1.0)    # gaze/heading (horizontal)
    eye_height_m: float = 1.45

    def as_dict(self):
        d = asdict(self)
        d["position_m"] = list(self.position_m)
        d["forward"] = list(self.forward)
        return d


@dataclass
class LightProfile:
    """Coarse lighting so content can be tinted/grounded to match the room."""
    intensity: float = 1000.0            # ARLightEstimate.ambientIntensity (lumens)
    color_temp_k: float = 6500.0         # ARLightEstimate.ambientColorTemperature
    key_direction: tuple = (0.0, -1.0, 0.0)  # best-effort primary light direction

    def as_dict(self):
        d = asdict(self)
        d["key_direction"] = list(self.key_direction)
        return d


@dataclass
class RoomModel:
    """The whole understood room. `free_floor_m` is a polygon (list of (x,z)) of
    walkable/placeable floor with clutter + a clearance margin removed."""
    surfaces: list = field(default_factory=list)        # list[Surface]
    obstacles: list = field(default_factory=list)        # list[Obstacle]
    free_floor_m: list = field(default_factory=list)     # [(x, z), ...] polygon
    user: UserPose = field(default_factory=UserPose)
    light: LightProfile = field(default_factory=LightProfile)
    bounds_m: tuple = (4.0, 3.0, 2.6)                    # (width, depth, ceiling)
    source: str = "default"                              # arkit|roomplan|cv|default|manual
    confidence: float = 1.0

    # -- convenience queries the solver uses -------------------------------------
    def surfaces_of(self, cls: str) -> list:
        return [s for s in self.surfaces if s.cls == cls]

    def best_table(self):
        tables = self.surfaces_of("table")
        return max(tables, key=lambda s: s.size_m[0] * s.size_m[1], default=None)

    def floor(self):
        floors = self.surfaces_of("floor")
        return max(floors, key=lambda s: s.size_m[0] * s.size_m[1], default=None)

    def as_dict(self):
        return {
            "schema": SCHEMA,
            "surfaces": [s.as_dict() for s in self.surfaces],
            "obstacles": [o.as_dict() for o in self.obstacles],
            "free_floor_m": [list(p) for p in self.free_floor_m],
            "user": self.user.as_dict(),
            "light": self.light.as_dict(),
            "bounds_m": list(self.bounds_m),
            "source": self.source,
            "confidence": self.confidence,
        }


# ── bridge to the existing coarse RoomProfile (analysis.py) ─────────────────────
# Lets the richer RoomModel flow into the current solver unchanged, and lets the
# old plane-only path keep working until the LiDAR producer lands.

def to_room_profile(rm: "RoomModel"):
    """Collapse a RoomModel down to the legacy RoomProfile analysis.estimate uses."""
    from analysis import RoomProfile  # studio/spatail on path
    floor = rm.floor()
    table = rm.best_table()
    fw = floor.size_m[0] if floor else rm.bounds_m[0]
    fd = floor.size_m[1] if floor else rm.bounds_m[1]
    return RoomProfile(
        floor_area_m2=float(fw * fd),
        floor_clear_w_m=float(fw), floor_clear_d_m=float(fd),
        table_present=table is not None,
        table_top_h_m=float(table.height_m) if table else 0.74,
        table_w_m=float(table.size_m[0]) if table else 1.2,
        table_d_m=float(table.size_m[1]) if table else 0.7,
        eye_height_m=float(rm.user.eye_height_m),
        source=rm.source,
    )


def from_room_profile(rp) -> "RoomModel":
    """Lift a coarse plane-only RoomProfile into a RoomModel (back-compat path)."""
    surfaces = [Surface(cls="floor", center_m=(0.0, 0.0, -1.5),
                        size_m=(rp.floor_clear_w_m, rp.floor_clear_d_m), height_m=0.0)]
    if rp.table_present:
        surfaces.append(Surface(cls="table", center_m=(0.0, rp.table_top_h_m, -0.74),
                                size_m=(rp.table_w_m, rp.table_d_m), height_m=rp.table_top_h_m))
    return RoomModel(
        surfaces=surfaces,
        user=UserPose(eye_height_m=rp.eye_height_m),
        bounds_m=(max(rp.floor_clear_w_m, 4.0), max(rp.floor_clear_d_m, 3.0), 2.6),
        source=rp.source,
    )


def demo() -> "RoomModel":
    """A representative living-room RoomModel for tests/authoring."""
    return RoomModel(
        surfaces=[
            Surface(cls="floor", center_m=(0.0, 0.0, -2.0), size_m=(4.0, 3.5), height_m=0.0),
            Surface(cls="table", center_m=(0.0, 0.74, -1.2), size_m=(1.2, 0.7), height_m=0.74),
            Surface(cls="wall", center_m=(0.0, 1.3, -3.6), size_m=(4.0, 2.6),
                    normal=(0.0, 0.0, 1.0)),
        ],
        obstacles=[Obstacle(bbox_min_m=(-1.8, 0.0, -2.8), bbox_max_m=(-1.0, 0.8, -2.0),
                            category="chair")],
        free_floor_m=[(-2.0, -0.4), (2.0, -0.4), (2.0, -3.4), (-2.0, -3.4)],
        user=UserPose(position_m=(0.0, 0.0, 0.0), forward=(0.0, 0.0, -1.0)),
        light=LightProfile(intensity=900.0, color_temp_k=4800.0),
        bounds_m=(4.0, 3.6, 2.6), source="demo",
    )


if __name__ == "__main__":
    import json
    rm = demo()
    print(json.dumps(rm.as_dict(), indent=2))
    print("floor:", rm.floor())
    print("best table:", rm.best_table())
