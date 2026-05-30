"""SPATAIL ANALYSIS — the engine's spatial brain.

"Show me your space; tell me what you want to see; I place it so it fits and is
comfortable." This module is the platform-neutral core of that loop. It does not
touch Blender or any device API — it reasons over a RoomProfile (which the iOS app
fills from an ARKit scan, the web viewer fills with sensible defaults, and tests
fill by hand) and an exhibit's real-world size, and returns viable SCALE VARIANTS
plus a comfort-aware placement for each.

The same rules run identically on Windows (build time), in the browser, and on
device — so what you preview is what you get in AR.

Frame: metres, +Y up (the AR/USDZ convention the device uses). The user stands at
the origin, eyes at RoomProfile.eye_height_m, looking -Z.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

# comfort constants live in xr_design (single source of truth); import lazily so
# this module also works standalone in environments without it.
try:
    import xr_design as xr
    NEAR, FOCAL, FAR = xr.NEAR_CLIP_M, xr.FOCAL_PLANE_M, xr.FAR_MAX_M
    READ = (xr.READ_NEAR_M, xr.READ_FAR_M)
    CONE, GAZE_DOWN, EYE = xr.OCPA_CONE_DEG, xr.GAZE_DOWN_DEG, xr.EYE_HEIGHT_M
except Exception:  # pragma: no cover - fallback mirrors xr_design
    NEAR, FOCAL, FAR = 0.37, 0.74, 10.0
    READ = (1.0, 1.5)
    CONE, GAZE_DOWN, EYE = 30.0, 12.0, 1.45


@dataclass
class RoomProfile:
    """What SPATAIL knows about the user's real space. iOS fills this from ARKit
    plane detection; everything else uses defaults. All metres."""
    floor_area_m2: float = 9.0          # detected horizontal floor extent
    floor_clear_w_m: float = 3.0        # usable clear width in front of the user
    floor_clear_d_m: float = 3.0        # usable clear depth in front of the user
    table_present: bool = True          # a horizontal surface ~desk height found?
    table_top_h_m: float = 0.74         # height of that surface
    table_w_m: float = 1.2
    table_d_m: float = 0.7
    eye_height_m: float = EYE
    source: str = "default"             # "arkit" | "default" | "manual"

    def as_dict(self):
        return asdict(self)


@dataclass
class Exhibit:
    """The thing we want to show, at its TRUE real-world size (metres)."""
    exhibit_id: str
    footprint_w_m: float                # widest horizontal extent
    footprint_d_m: float
    height_m: float
    title: str = ""

    def as_dict(self):
        return asdict(self)


@dataclass
class ScaleVariant:
    """One viable way to show the exhibit, with the placement SPATAIL chose."""
    name: str                           # "tabletop" | "real"
    scale: float                        # multiply exhibit real size by this
    anchor: str                         # "table" | "floor"
    position_yup_m: tuple               # where the exhibit ORIGIN goes (x,y,z)
    fits: bool
    reason: str
    comfort_distance_m: float
    scaled_size_m: tuple

    def as_dict(self):
        d = asdict(self)
        d["position_yup_m"] = list(self.position_yup_m)
        d["scaled_size_m"] = list(self.scaled_size_m)
        return d


def _baseline_y(distance_m: float, eye: float) -> float:
    """Vertical centre for content at distance, dropped along the natural gaze."""
    return eye - distance_m * math.tan(math.radians(GAZE_DOWN))


def estimate_variants(exhibit: Exhibit, room: RoomProfile) -> list[ScaleVariant]:
    """Return the viable scale variants for this exhibit in this room, ordered by
    how natural they are. Always returns at least one variant (we never refuse to
    show something — if nothing fits cleanly we return the best-effort tabletop
    with fits=False and a reason)."""
    variants: list[ScaleVariant] = []
    w, d, h = exhibit.footprint_w_m, exhibit.footprint_d_m, exhibit.height_m

    # --- TABLETOP: shrink so the whole exhibit fits a desk, viewed at reading distance
    # target footprint ~ a comfortable diorama on the available (or assumed) table
    target_w = min(0.6, (room.table_w_m * 0.7) if room.table_present else 0.6)
    tscale = min(1.0, target_w / w) if w > 0 else 1.0
    tw, td, th = w * tscale, d * tscale, h * tscale
    # place on the table surface, centred, pushed to a comfortable look-down distance
    tdist = max(READ[0], min(READ[1], 0.6))
    table_h = room.table_top_h_m if room.table_present else 0.74
    tabletop = ScaleVariant(
        name="tabletop",
        scale=round(tscale, 4),
        anchor="table" if room.table_present else "floor",
        position_yup_m=(0.0, table_h, -tdist),
        fits=True,
        reason=(f"Shrunk x{tscale:.2f} to a {tw:.2f}x{td:.2f} m diorama on the "
                f"{'detected table' if room.table_present else 'floor'} at "
                f"{table_h:.2f} m, viewed at {tdist:.2f} m (reading band)."),
        comfort_distance_m=round(tdist, 3),
        scaled_size_m=(round(tw, 4), round(th, 4), round(td, 4)),
    )
    variants.append(tabletop)

    # --- REAL SCALE: true size on the floor, viewed from far enough to see it all
    # distance so the exhibit subtends within the comfort cone horizontally
    half_cone = math.radians(CONE / 2)
    need_dist = (w / 2) / math.tan(half_cone) if w > 0 else NEAR
    rdist = max(NEAR + w / 2, need_dist)
    clear = min(room.floor_clear_d_m, room.floor_clear_w_m)
    real_fits = (rdist + d / 2) <= room.floor_clear_d_m and w <= room.floor_clear_w_m and rdist <= FAR
    real = ScaleVariant(
        name="real",
        scale=1.0,
        anchor="floor",
        position_yup_m=(0.0, 0.0, -round(rdist, 3)),
        fits=bool(real_fits),
        reason=(f"True size ({w:.2f}x{d:.2f}x{h:.2f} m) on the floor at "
                f"{rdist:.2f} m so it sits inside the {CONE:.0f}deg comfort cone. "
                + ("Fits your clear space." if real_fits else
                   f"Needs ~{rdist + d/2:.1f} m depth / {w:.1f} m width; your clear "
                   f"space is ~{room.floor_clear_d_m:.1f}x{room.floor_clear_w_m:.1f} m "
                   f"— move back or choose tabletop.")),
        comfort_distance_m=round(rdist, 3),
        scaled_size_m=(round(w, 4), round(h, 4), round(d, 4)),
    )
    variants.append(real)

    # order: prefer the real scale when it fits the space, else tabletop first
    variants.sort(key=lambda v: (0 if (v.fits and v.name == "real") else
                                 1 if v.name == "tabletop" else 2))
    return variants


def analyze(exhibit: Exhibit, room: RoomProfile) -> dict:
    """Top-level SPATAIL ANALYSIS result: the room it saw, the exhibit it was
    asked for, the scale options, and a recommendation. This dict is what the
    device shows the user ('here's what fits — tabletop or real?')."""
    variants = estimate_variants(exhibit, room)
    recommended = variants[0].name
    return {
        "schema": "spatail-analysis/1",
        "room": room.as_dict(),
        "exhibit": exhibit.as_dict(),
        "variants": [v.as_dict() for v in variants],
        "recommended": recommended,
        "summary": (f"For '{exhibit.title or exhibit.exhibit_id}' "
                    f"({exhibit.footprint_w_m:.2f} m wide) in your "
                    f"{room.floor_clear_w_m:.1f}x{room.floor_clear_d_m:.1f} m space: "
                    f"recommend {recommended}."),
    }


if __name__ == "__main__":
    import json
    demo = Exhibit("law2_fma", 0.92, 0.3, 0.45, "Inclined plane (F=ma)")
    small = RoomProfile(floor_clear_w_m=1.6, floor_clear_d_m=1.4, table_present=True)
    big = RoomProfile(floor_clear_w_m=4.0, floor_clear_d_m=4.0, table_present=False, source="arkit")
    print("== small room ==");  print(json.dumps(analyze(demo, small), indent=2))
    print("== big room ==");    print(json.dumps(analyze(demo, big), indent=2))
