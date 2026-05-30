"""XR comfort + placement constants and helpers for the SPATAIL Studio.

Single source of truth for "where does content go so it is comfortable to look
at and easy to understand." The numbers come from Apple's visionOS Human
Interface Guidelines and Magic Leap's spatial-design guidance (citations in
studio/README.md). The Senior Developer role places every asset through these
helpers, so spatial layout is principled rather than hand-tuned — and the same
rules can later drive the real XR runtime.

Frame: Blender-native — metres, +Z up, +Y forward (pointing away from the
user). The user stands at the world origin with eyes at (0, 0, EYE_HEIGHT_M).
The glTF exporter (+Y up) maps this to the three.js viewer as:
Blender +Y -> -Z (into the screen), Blender +Z -> +Y (up), Blender +X -> +X.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

# --- Depth / distance, metres -------------------------------------------------
NEAR_CLIP_M = 0.37      # ML: never place content closer; vergence-accommodation strain
FOCAL_PLANE_M = 0.74    # ML: comfort focal plane — manipulable 3D objects belong here
READ_NEAR_M = 1.0       # AVP: floating text reads best just past arm's reach
READ_FAR_M = 1.5
FAR_MAX_M = 10.0        # ML: optical-infinity comfort ceiling

# --- Angles, degrees ----------------------------------------------------------
OCPA_CONE_DEG = 30.0    # ML: primary content inside a 30 deg cone needs no head turn
EYE_COMFORT_DEG = 15.0  # ML: eyes rotate ~+/-15 deg before the head must move
GAZE_DOWN_DEG = 12.0    # ML: natural line of sight rests 10-15 deg below the horizon

# --- The user -----------------------------------------------------------------
EYE_HEIGHT_M = 1.45     # standing adult eye height in the tester room

# --- Reach, metres (ML hand-tracking guidance) --------------------------------
DIRECT_REACH_M = (0.40, 0.60)   # comfortable reach-and-grab
INDIRECT_M = (0.80, 1.50)       # ray / gaze-pinch at distance
MAX_REACH_M = 0.75              # never require sustained reach past this

# --- Targets / rendering ------------------------------------------------------
MIN_TARGET_DEG = 2.5    # AVP: 60 pt ~= 2.5 deg ~= 4.4 cm at 1 m — min hit target
TARGET_FPS = 90         # ML: 90-120 ideal, 60 floor — fewer = more motion sickness

# --- Studio "tester room", metres ---------------------------------------------
ROOM_W = 6.0
ROOM_D = 6.0
ROOM_H = 3.0


def clamp_distance(d: float) -> float:
    """Pull any requested distance into the comfortable [near, far] band."""
    return max(NEAR_CLIP_M, min(FAR_MAX_M, d))


def baseline_height(distance_m: float, gaze_down_deg: float = GAZE_DOWN_DEG,
                    eye: float = EYE_HEIGHT_M) -> float:
    """Z (height) for the centre of content at `distance_m`, dropped below eye
    level along the natural downward gaze so the user never cranes upward."""
    return eye - distance_m * math.tan(math.radians(gaze_down_deg))


def focal_point(distance_m: float = FOCAL_PLANE_M) -> tuple[float, float, float]:
    """Centred content directly ahead at the focal plane. (x, y_forward, z_up)."""
    d = clamp_distance(distance_m)
    return (0.0, d, baseline_height(d))


def arc_positions(n: int, distance_m: float = FOCAL_PLANE_M,
                  spread_deg: float | None = None,
                  eye: float = EYE_HEIGHT_M) -> list[tuple[float, float, float]]:
    """`n` slots on a horizontal arc curved toward the user: equidistant at
    `distance_m`, centred on forward, fanned across the comfort cone. Curving
    (vs. a flat row) keeps every slot the same distance from the eyes — the
    Magic Leap "curve layouts toward the user" rule. Returns Blender (x, y, z)."""
    d = clamp_distance(distance_m)
    if spread_deg is None:
        spread_deg = OCPA_CONE_DEG if n > 1 else 0.0
    z = baseline_height(d, eye=eye)
    if n <= 1:
        return [(0.0, d, z)]
    half = spread_deg / 2.0
    out = []
    for i in range(n):
        t = i / (n - 1)                       # 0..1 left to right
        ang = math.radians(-half + t * spread_deg)
        out.append((d * math.sin(ang), d * math.cos(ang), z))
    return out


def yaw_to_face_user(x: float, y: float) -> float:
    """Z-rotation (radians) that turns a panel authored facing -Y (toward the
    user) so its face stays square to the user when placed off-centre at (x,y)."""
    return math.atan2(-x, y)


def angular_size_m(distance_m: float, deg: float = MIN_TARGET_DEG) -> float:
    """Real-world size (m) that subtends `deg` at `distance_m`. Use it to size
    text/targets so they stay legible — min target is MIN_TARGET_DEG."""
    return 2.0 * distance_m * math.tan(math.radians(deg / 2.0))


def in_comfort_cone(pos: tuple[float, float, float],
                    eye: float = EYE_HEIGHT_M,
                    cone_deg: float = OCPA_CONE_DEG) -> bool:
    """True if `pos` (Blender frame) falls inside the no-head-turn cone."""
    x, y, z = pos
    if y <= 0:
        return False
    horiz = math.degrees(math.atan2(abs(x), y))
    vert = math.degrees(math.atan2(abs(z - eye), y))
    return horiz <= cone_deg / 2.0 and vert <= cone_deg / 2.0


CITATIONS = {
    "NEAR_CLIP_M": "Magic Leap — Display Zone / Vergence-Accommodation Conflict",
    "FOCAL_PLANE_M": "Magic Leap — Display Zone (comfort focal plane)",
    "READ_NEAR_M": "Apple visionOS HIG — text beyond arm's reach reads best",
    "OCPA_CONE_DEG": "Magic Leap — Optimal Content Placement Area (30 deg cone)",
    "GAZE_DOWN_DEG": "Magic Leap — natural line of sight 10-15 deg below horizon",
    "MIN_TARGET_DEG": "Apple visionOS HIG — 60 pt minimum interactive target",
    "TARGET_FPS": "Magic Leap — 90-120 fps for comfort",
}


def as_dict() -> dict:
    """Flat, serialisable mirror of every constant — consumed by the JS viewer
    (to draw comfort guides) and the contract builder (to record reasoning)."""
    return {
        "frame": {
            "units": "m", "up": "+Z", "forward": "+Y",
            "note": "Blender-native; glTF +Y-up export maps +Y->-Z, +Z->+Y for three.js",
        },
        "distances_m": {
            "near_clip": NEAR_CLIP_M, "focal_plane": FOCAL_PLANE_M,
            "read_near": READ_NEAR_M, "read_far": READ_FAR_M, "far_max": FAR_MAX_M,
            "direct_reach": list(DIRECT_REACH_M), "indirect": list(INDIRECT_M),
            "max_reach": MAX_REACH_M,
        },
        "angles_deg": {
            "ocpa_cone": OCPA_CONE_DEG, "eye_comfort": EYE_COMFORT_DEG,
            "gaze_down": GAZE_DOWN_DEG, "min_target": MIN_TARGET_DEG,
        },
        "user": {"eye_height_m": EYE_HEIGHT_M},
        "room_m": {"w": ROOM_W, "d": ROOM_D, "h": ROOM_H},
        "render": {"target_fps": TARGET_FPS},
        "citations": CITATIONS,
    }


if __name__ == "__main__":
    out = Path(__file__).with_suffix(".json")
    out.write_text(json.dumps(as_dict(), indent=2), encoding="utf-8")
    print(f"wrote {out}")
