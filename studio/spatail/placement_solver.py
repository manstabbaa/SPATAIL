"""placement_solver.py — the SPATAIL Placement Design System (Pillar 2).

Pure function: solve(RoomModel + asset requirements + placement intent) -> PlacementPlan.
This is the canonical algorithm; it runs at AUTHORING/preview/test time here, and is
mirrored on-device in Swift (PlacementSolver.swift) so what you preview is what the
phone places. It NEVER touches a device API or Blender — it only reasons over the
RoomModel (the understood room) and the assets' true sizes.

Spatial level-design vocabulary:
  * pick an ANCHOR SURFACE from the room (table preferred, else floor) honouring intent
  * choose a SCALE VARIANT so the set fits the surface and the comfort cone
  * lay assets on a comfort ARC curved toward the user (equidistant, no head-turn)
  * assign ZONES (hero = primary, centred & nearest; secondary fanned; peripheral)
  * keep everything inside the surface extent / free floor and out of obstacles

Frame: metres, +Y up, user near origin looking -Z (matches analysis.py + the device).
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE), str(_HERE.parent)):           # studio/spatail + studio (xr_design)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import room_model as rmod  # noqa: E402

try:
    import xr_design as xr
    NEAR, FOCAL = xr.NEAR_CLIP_M, xr.FOCAL_PLANE_M
    READ_NEAR, READ_FAR = xr.READ_NEAR_M, xr.READ_FAR_M
    CONE_DEG, GAZE_DOWN, EYE = xr.OCPA_CONE_DEG, xr.GAZE_DOWN_DEG, xr.EYE_HEIGHT_M
except Exception:  # pragma: no cover
    NEAR, FOCAL, READ_NEAR, READ_FAR = 0.37, 0.74, 1.0, 1.5
    CONE_DEG, GAZE_DOWN, EYE = 30.0, 12.0, 1.45

SCHEMA = "spatail-placement-plan/1"


@dataclass
class Placement:
    assetId: str
    position_m: tuple            # (x, y, z) in +Y-up, user at origin looking -Z
    yaw_rad: float              # rotation about Y to face the user
    scale: float               # multiply the asset's authored size by this
    anchor: str                # "table" | "floor"
    zone: str                  # "hero" | "secondary" | "peripheral"
    fits: bool
    reason: str

    def as_dict(self):
        d = asdict(self)
        d["position_m"] = [round(v, 4) for v in self.position_m]
        d["yaw_rad"] = round(self.yaw_rad, 4)
        d["scale"] = round(self.scale, 4)
        return d


@dataclass
class PlacementPlan:
    placements: list = field(default_factory=list)
    anchor: str = "table"
    scaleVariant: str = "tabletop"          # tabletop | real
    diagnostics: dict = field(default_factory=dict)

    def as_dict(self):
        return {
            "schema": SCHEMA,
            "anchor": self.anchor,
            "scaleVariant": self.scaleVariant,
            "placements": [p.as_dict() for p in self.placements],
            "diagnostics": self.diagnostics,
        }


def _arc(n: int, distance: float, height: float, spread_deg: float):
    """`n` slots on a horizontal arc curved toward the user at constant `distance`,
    fanned across `spread_deg`, at `height`. Returns [(x, y, z), yaw] per slot —
    centre-out ordering so the hero (index 0) lands dead ahead."""
    if n <= 0:
        return []
    if n == 1:
        return [((0.0, height, -distance), 0.0)]
    half = spread_deg / 2.0
    # order indices centre-out: 0 -> centre, then alternate sides
    order = [0]
    for k in range(1, n):
        order.append((k + 1) // 2 * (1 if k % 2 else -1))
    lo, hi = min(order), max(order)
    out = []
    for slot in order:
        t = (slot - lo) / (hi - lo) if hi != lo else 0.5      # 0..1 left->right
        ang = math.radians(-half + t * spread_deg)
        x = distance * math.sin(ang)
        z = -distance * math.cos(ang)
        yaw = math.atan2(x, distance)                         # turn to face the user
        out.append(((x, height, z), yaw))
    return out


def solve(room: "rmod.RoomModel", assets: list[dict], intent: dict | None = None) -> PlacementPlan:
    """assets: [{id, footprint:[w,d,h] metres, role, realScaleBaked?}]; intent:
    {anchorPreference, layout, scaleMode, primary, coverage}. Returns a
    PlacementPlan in the device frame."""
    intent = intent or {}
    n = len(assets)
    if n == 0:
        return PlacementPlan(diagnostics={"note": "no assets"})

    pref = intent.get("anchorPreference", "table")
    scale_mode = intent.get("scaleMode", "dynamic")
    # usable fraction of the surface width (design system §5) — clamped like the
    # Swift solver so both sides agree on the same fixture
    try:
        coverage = float(intent.get("coverage", 0.8))
    except (TypeError, ValueError):
        coverage = 0.8
    coverage = min(max(coverage, 0.2), 0.95)
    primary = intent.get("primary") or assets[0]["id"]
    # put the primary first (hero, centred)
    assets = sorted(assets, key=lambda a: 0 if a["id"] == primary else 1)

    table = room.best_table()
    floor = room.floor()
    use_table = (pref in ("table", "free") and table is not None and scale_mode != "real")

    if use_table:
        anchor = "table"
        surf_w, surf_d = table.size_m
        base_h = table.height_m
        distance = max(READ_NEAR * 0.6, min(0.7, READ_NEAR))   # reading-ish, close look-down
        variant = "tabletop"
    else:
        anchor = "floor"
        if floor is not None:
            surf_w, surf_d = floor.size_m
        else:
            surf_w, surf_d = room.bounds_m[0], room.bounds_m[1]
        base_h = 0.0
        distance = max(READ_FAR, 1.2)
        variant = "real" if scale_mode == "real" else "tabletop"

    # total authored footprint width + gaps the set needs at scale 1
    widths = [max(a.get("footprint", [0.2, 0.2, 0.2])[0], 0.03) for a in assets]
    gap = 0.12
    needed_w = sum(widths) + gap * (n - 1)
    avail_w = max(0.2, surf_w * coverage)                     # coverage of surface usable
    fit_scale = min(1.0, avail_w / needed_w) if needed_w > 0 else 1.0
    # A metric-baked hero renders at scale 1.0 (the render path enforces it), so keep
    # the solver in agreement — don't coverage-shrink the holder out from under it.
    # (Anchor choice is unchanged: a baked asset still sits on the table/floor it was
    # assigned; only the uniform fit scale is pinned to 1.0.)
    hero_baked = bool(assets[0].get("realScaleBaked", False))
    scale = 1.0 if (variant == "real" or hero_baked) else fit_scale

    # arc spread: wider for more items, capped to the comfort cone
    spread = min(CONE_DEG, 8.0 + 7.0 * max(0, n - 1))
    height = base_h + 0.0                                     # asset origins sit on the surface
    slots = _arc(n, distance, height, spread)

    # keep-out only against obstacles that REST ON the chosen surface — floor
    # furniture (a chair tucked under a table) is shielded by the tabletop, so it
    # must not block a table placement (mirrors the Blender placer's _on_support
    # gate). For a floor placement base_h = 0, so floor obstacles still count.
    on_surface = [o for o in room.obstacles if o.bbox_min_m[1] >= base_h - 0.05]
    obstacles_xz = [((o.bbox_min_m[0], o.bbox_min_m[2]), (o.bbox_max_m[0], o.bbox_max_m[2]))
                    for o in on_surface]

    placements = []
    for i, a in enumerate(assets):
        (x, y, z), yaw = slots[i]
        zone = "hero" if i == 0 else ("secondary" if i <= 2 else "peripheral")
        # keep within surface half-extent
        half_w = surf_w / 2.0
        fits = abs(x) <= half_w and (variant != "real" or surf_d >= distance)
        blocked = any(mn[0] <= x <= mx[0] and mn[1] <= z <= mx[1] for mn, mx in obstacles_xz)
        if blocked:
            fits = False
        reason = (f"{zone} on {anchor} at {distance:.2f} m, scaled x{scale:.2f}"
                  + ("" if fits else "; tight/out-of-bounds — consider fewer items or floor scale"))
        placements.append(Placement(
            assetId=a["id"], position_m=(x, y, z), yaw_rad=yaw, scale=scale,
            anchor=anchor, zone=zone, fits=bool(fits), reason=reason))

    return PlacementPlan(
        placements=placements, anchor=anchor, scaleVariant=variant,
        diagnostics={
            "assets": n, "surface_m": [round(surf_w, 3), round(surf_d, 3)],
            "needed_w_m": round(needed_w, 3), "fit_scale": round(fit_scale, 3),
            "distance_m": round(distance, 3), "spread_deg": round(spread, 1),
            "obstacles": len(room.obstacles), "source": room.source,
            "coverage": round(coverage, 3),
            "obstacles_on_surface": len(obstacles_xz),
            "hero_baked": hero_baked,
        })


def solve_from_dicts(room_dict: dict, assets: list[dict], intent: dict | None = None) -> dict:
    """Convenience: accept a serialized RoomModel + return a serialized plan (the
    shape the device/server exchange)."""
    rm = _room_from_dict(room_dict)
    return solve(rm, assets, intent).as_dict()


def _room_from_dict(d: dict) -> "rmod.RoomModel":
    surfaces = [rmod.Surface(cls=s["cls"], center_m=tuple(s.get("center_m", (0, 0, 0))),
                             size_m=tuple(s.get("size_m", (1, 1))),
                             normal=tuple(s.get("normal", (0, 1, 0))),
                             yaw_rad=s.get("yaw_rad", 0.0), height_m=s.get("height_m", 0.0),
                             confidence=s.get("confidence", 1.0)) for s in d.get("surfaces", [])]
    obstacles = [rmod.Obstacle(bbox_min_m=tuple(o.get("bbox_min_m", (0, 0, 0))),
                               bbox_max_m=tuple(o.get("bbox_max_m", (0, 0, 0))),
                               category=o.get("category", "unknown")) for o in d.get("obstacles", [])]
    u = d.get("user", {})
    return rmod.RoomModel(surfaces=surfaces, obstacles=obstacles,
                          free_floor_m=[tuple(p) for p in d.get("free_floor_m", [])],
                          user=rmod.UserPose(eye_height_m=u.get("eye_height_m", EYE)),
                          bounds_m=tuple(d.get("bounds_m", (4, 3, 2.6))),
                          source=d.get("source", "default"))


if __name__ == "__main__":
    import json
    room = rmod.demo()
    assets = [
        {"id": "sun", "footprint": [0.25, 0.25, 0.25], "role": "primary_object"},
        {"id": "earth", "footprint": [0.12, 0.12, 0.12], "role": "comparison_object"},
        {"id": "moon", "footprint": [0.06, 0.06, 0.06], "role": "part"},
    ]
    plan = solve(room, assets, {"anchorPreference": "table", "scaleMode": "dynamic", "primary": "sun"})
    print(json.dumps(plan.as_dict(), indent=2))

    # selftest — the canonical richness + the Swift back-ported behaviors
    d = plan.as_dict()
    assert d["anchor"] == "table" and d["scaleVariant"] == "tabletop"
    assert all(p["fits"] and p["reason"] for p in d["placements"])
    assert d["placements"][0]["zone"] == "hero" and d["placements"][0]["assetId"] == "sun"
    for k in ("assets", "surface_m", "needed_w_m", "fit_scale", "distance_m",
              "spread_deg", "obstacles", "source", "coverage",
              "obstacles_on_surface", "hero_baked"):
        assert k in d["diagnostics"], k

    # 1) obstacle filtering: the demo chair is FLOOR furniture -> it never blocks a
    #    TABLE placement, but it still counts against a floor placement at its spot.
    assert d["diagnostics"]["obstacles"] == 1 and d["diagnostics"]["obstacles_on_surface"] == 0
    hero_xz = d["placements"][0]["position_m"]
    blocked_room = rmod.demo()
    blocked_room.obstacles.append(rmod.Obstacle(
        bbox_min_m=(hero_xz[0] - 0.1, 0.70, hero_xz[2] - 0.1),
        bbox_max_m=(hero_xz[0] + 0.1, 1.00, hero_xz[2] + 0.1), category="plant"))
    blocked = solve(blocked_room, assets,
                    {"anchorPreference": "table", "scaleMode": "dynamic", "primary": "sun"}).as_dict()
    assert blocked["diagnostics"]["obstacles_on_surface"] == 1
    assert not blocked["placements"][0]["fits"]
    assert "out-of-bounds" in blocked["placements"][0]["reason"]   # reason strings kept

    # 2) coverage: tighter budget -> smaller fit scale (default 0.8 preserved above)
    tight = solve(room, assets, {"anchorPreference": "table", "scaleMode": "dynamic",
                                 "primary": "sun", "coverage": 0.4}).as_dict()
    assert tight["diagnostics"]["coverage"] == 0.4
    assert tight["placements"][0]["scale"] < d["placements"][0]["scale"]

    # 3) baked-scale pinning: a metric-baked hero locks the uniform scale to 1.0
    #    even when the set would otherwise coverage-shrink.
    big = [
        {"id": "engine", "footprint": [0.9, 0.6, 0.6], "role": "primary_object",
         "realScaleBaked": True},
        {"id": "piston", "footprint": [0.5, 0.3, 0.3], "role": "part"},
    ]
    baked = solve(room, big, {"anchorPreference": "table", "scaleMode": "dynamic",
                              "primary": "engine"}).as_dict()
    assert baked["diagnostics"]["hero_baked"] is True
    assert baked["diagnostics"]["fit_scale"] < 1.0        # it WOULD have shrunk
    assert all(p["scale"] == 1.0 for p in baked["placements"])
    unbaked = solve(room, [dict(big[0], realScaleBaked=False), big[1]],
                    {"anchorPreference": "table", "scaleMode": "dynamic",
                     "primary": "engine"}).as_dict()
    assert unbaked["placements"][0]["scale"] < 1.0

    # 4) real-scale variant unchanged: floor anchor, scale 1.0
    real = solve(room, big, {"anchorPreference": "floor", "scaleMode": "real",
                             "primary": "engine"}).as_dict()
    assert real["anchor"] == "floor" and real["scaleVariant"] == "real"
    assert all(p["scale"] == 1.0 for p in real["placements"])
    print("SELFTEST PASS")
