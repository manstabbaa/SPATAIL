"""catalog.py — the SPATAIL modular mechanics library (source of truth).

A "mechanic" is one composable unit of presentation the runtime can execute on an
asset, a group, or the scene: an animation, a reveal, a focus change, a comparison,
an annotation, an interaction, a camera move, or narration. The Director agent
composes these freely into an experience; every platform runtime (iOS / visionOS /
web) implements them generically by switching on `runtime`. Adding a capability =
add a Mechanic here + implement its `runtime` key once per platform.

build.py emits public/mechanics/mechanics.json from this file so the runtimes and the
agent prompt share one definition. Keep this list platform-agnostic and declarative.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# ── capability tokens an asset / scene can satisfy (gate which mechanics apply) ──
CAPABILITIES = {
    "none",          # always usable
    "animation",     # asset carries a baked clip the mechanic can play/scrub
    "named_parts",   # multi-part asset (explode/assemble/isolate a part)
    "two_or_more",   # scene has >= 2 assets (comparison)
    "transparency",  # material supports alpha (ghost/cutaway)
    "physics",       # asset has a collider/mass (grab)
    "path",          # a path/trajectory is defined (flow/trace)
}

# parameter types the agent + validator understand
PTYPES = {"float", "int", "bool", "enum", "vec3", "color", "string", "assetRef", "axis"}


def P(default, typ, rng=None, doc=""):
    """One typed parameter: default value, type, range/options, one-line doc."""
    return {"default": default, "type": typ, "range": rng, "doc": doc}


@dataclass
class Mechanic:
    id: str
    category: str            # motion|reveal|focus|compare|annotate|interact|camera|narrate
    summary: str
    runtime: str             # impl key the platform runners switch on
    needs: list              # capability tokens (all required); [] => always usable
    params: dict             # name -> P(...)
    triggers: list           # allowed: auto|on_tap|on_focus|on_step|on_grab|on_slider|timeline
    default_trigger: str
    platforms: list = field(default_factory=lambda: ["ios", "visionos", "web"])

    def to_dict(self) -> dict:
        return asdict(self)


AXES = ["x", "y", "z"]
_TRIG_ALL = ["auto", "on_tap", "on_focus", "on_step"]

MECHANICS: list[Mechanic] = [
    # ── motion ────────────────────────────────────────────────────────────────
    Mechanic("spin", "motion", "Rotate continuously about an axis (wheels, planets, drums).",
             "transform.spin", [], {"axis": P("y", "axis", AXES), "rpm": P(12.0, "float", [0.5, 120]),
             "direction": P(1, "int", [-1, 1])}, _TRIG_ALL, "auto"),
    Mechanic("orbit", "motion", "Move one asset around another on a circular path.",
             "transform.orbit", [], {"center": P("", "assetRef"), "radius_m": P(0.3, "float", [0.02, 3]),
             "period_s": P(6.0, "float", [0.5, 60]), "axis": P("y", "axis", AXES)}, _TRIG_ALL, "auto"),
    Mechanic("oscillate", "motion", "Swing back and forth about a pivot (pendulum, swing, metronome).",
             "transform.oscillate", [], {"axis": P("x", "axis", AXES), "amplitude_deg": P(25.0, "float", [1, 90]),
             "period_s": P(2.0, "float", [0.2, 20]), "pivot": P("top", "enum", ["top", "center", "bottom"])},
             _TRIG_ALL, "auto"),
    Mechanic("reciprocate", "motion", "Linear back-and-forth (piston, plunger, needle).",
             "transform.reciprocate", [], {"axis": P("z", "axis", AXES), "distance_m": P(0.1, "float", [0.005, 1]),
             "period_s": P(1.0, "float", [0.1, 10])}, _TRIG_ALL, "auto"),
    Mechanic("bob", "motion", "Gentle float up/down to feel alive (idle hover).",
             "transform.bob", [], {"amplitude_m": P(0.02, "float", [0.002, 0.2]), "period_s": P(3.0, "float", [0.5, 10])},
             ["auto"], "auto"),
    Mechanic("grow_shrink", "motion", "Scale between two sizes (growth, breathing, inflation).",
             "transform.scale", [], {"from": P(1.0, "float", [0.05, 5]), "to": P(1.4, "float", [0.05, 5]),
             "period_s": P(2.5, "float", [0.2, 30]), "loop": P(True, "bool")}, _TRIG_ALL, "auto"),
    Mechanic("flow", "motion", "Stream markers/particles along a path (current, blood, traffic).",
             "fx.flow", ["path"], {"speed": P(0.3, "float", [0.02, 3]), "count": P(20, "int", [3, 200]),
             "color": P("#54b0ff", "color")}, _TRIG_ALL, "auto"),
    Mechanic("wave", "motion", "Propagate a wave along an axis (sound, ripple, oscillation).",
             "fx.wave", [], {"axis": P("x", "axis", AXES), "amplitude_m": P(0.05, "float", [0.005, 0.5]),
             "wavelength_m": P(0.3, "float", [0.05, 2]), "speed": P(0.4, "float", [0.05, 3])}, _TRIG_ALL, "auto"),
    Mechanic("play_clip", "motion", "Play the asset's baked animation clip.",
             "anim.play", ["animation"], {"clip": P("", "string"), "loop": P(True, "bool"),
             "speed": P(1.0, "float", [0.1, 4])}, _TRIG_ALL, "auto"),

    # ── reveal / assembly ──────────────────────────────────────────────────────
    Mechanic("assemble", "reveal", "Parts fly in and snap together to build the whole.",
             "parts.assemble", ["named_parts"], {"duration_s": P(2.0, "float", [0.3, 10]),
             "stagger_s": P(0.15, "float", [0, 1]), "from_m": P(0.4, "float", [0.05, 2])}, _TRIG_ALL, "on_step"),
    Mechanic("explode", "reveal", "Separate parts outward to show how it's built.",
             "parts.explode", ["named_parts"], {"distance_m": P(0.25, "float", [0.02, 1.5]),
             "axis": P("radial", "enum", ["radial", "x", "y", "z"]), "stagger_s": P(0.08, "float", [0, 1])},
             _TRIG_ALL, "on_tap"),
    Mechanic("cutaway", "reveal", "Slice the object with a plane to expose the interior.",
             "geo.cutaway", ["transparency"], {"plane": P("x", "axis", AXES), "offset_m": P(0.0, "float", [-0.5, 0.5])},
             _TRIG_ALL, "on_tap"),
    Mechanic("fade_in", "reveal", "Fade assets in (optionally staggered) as they're introduced.",
             "material.fade", [], {"duration_s": P(0.6, "float", [0.1, 3]), "stagger_s": P(0.1, "float", [0, 1])},
             ["auto", "on_step"], "auto"),
    Mechanic("build_up", "reveal", "Reveal parts one at a time in a meaningful order.",
             "parts.sequence", ["named_parts"], {"per_part_s": P(0.5, "float", [0.1, 3]),
             "order": P("bottom_up", "enum", ["bottom_up", "top_down", "back_to_front", "given"])},
             ["auto", "on_step"], "on_step"),
    Mechanic("dissolve", "reveal", "Dissolve a layer/asset away to reveal what's beneath.",
             "material.dissolve", ["transparency"], {"duration_s": P(1.0, "float", [0.2, 5])}, _TRIG_ALL, "on_tap"),

    # ── focus ──────────────────────────────────────────────────────────────────
    Mechanic("isolate", "focus", "Hide everything except the focused asset/part.",
             "focus.isolate", [], {"target": P("", "assetRef")}, ["on_tap", "on_focus", "on_step"], "on_tap"),
    Mechanic("ghost_others", "focus", "Make non-focused assets semi-transparent.",
             "focus.ghost", ["transparency"], {"opacity": P(0.18, "float", [0.0, 0.6])},
             ["on_focus", "on_step", "auto"], "on_focus"),
    Mechanic("highlight", "focus", "Tint/emit a target to draw the eye.",
             "focus.highlight", [], {"target": P("", "assetRef"), "color": P("#ffd23f", "color"),
             "pulse": P(True, "bool")}, _TRIG_ALL, "on_focus"),
    Mechanic("zoom_to", "focus", "Scale a target up in place for close inspection.",
             "focus.zoom", [], {"target": P("", "assetRef"), "scale": P(1.8, "float", [1.05, 5])},
             ["on_tap", "on_focus", "on_step"], "on_tap"),
    Mechanic("pulse", "focus", "Rhythmically pulse a target's scale to mark it as live/important.",
             "focus.pulse", [], {"target": P("", "assetRef"), "period_s": P(1.2, "float", [0.3, 5]),
             "amount": P(0.08, "float", [0.01, 0.4])}, ["auto", "on_focus"], "auto"),

    # ── compare ────────────────────────────────────────────────────────────────
    Mechanic("side_by_side", "compare", "Lay items in a row at matched scale to compare.",
             "compare.row", ["two_or_more"], {"gap_m": P(0.12, "float", [0.01, 1]),
             "match_scale": P(True, "bool")}, ["auto", "on_step"], "auto"),
    Mechanic("scale_compare", "compare", "Show items at TRUE relative size against a referent.",
             "compare.scale", ["two_or_more"], {"referent": P("", "assetRef")}, ["auto", "on_step"], "auto"),
    Mechanic("before_after", "compare", "Swap/cross-fade between two states of the same thing.",
             "compare.beforeafter", [], {"a": P("", "assetRef"), "b": P("", "assetRef"),
             "transition": P("crossfade", "enum", ["crossfade", "wipe", "swap"])}, ["on_tap", "on_step"], "on_tap"),
    Mechanic("morph", "compare", "Smoothly morph one asset/state into another.",
             "compare.morph", [], {"from": P("", "assetRef"), "to": P("", "assetRef"),
             "duration_s": P(1.5, "float", [0.2, 8])}, ["on_tap", "on_step"], "on_tap"),

    # ── annotate ───────────────────────────────────────────────────────────────
    Mechanic("label", "annotate", "Floating label pinned to a target.",
             "ui.label", [], {"target": P("", "assetRef"), "text": P("", "string"),
             "anchor": P("top", "enum", ["top", "front", "left", "right"])}, ["auto", "on_focus"], "auto"),
    Mechanic("callout", "annotate", "Label with a leader line to a precise point.",
             "ui.callout", [], {"target": P("", "assetRef"), "text": P("", "string")}, ["auto", "on_focus"], "auto"),
    Mechanic("dimension_line", "annotate", "Draw a measured dimension between two points.",
             "ui.dimension", [], {"from": P("", "assetRef"), "to": P("", "assetRef"), "value": P("", "string")},
             ["auto", "on_step"], "auto"),
    Mechanic("arrow_point", "annotate", "Arrow pointing at a feature, optional caption.",
             "ui.arrow", [], {"at": P("", "assetRef"), "text": P("", "string")}, ["auto", "on_focus"], "auto"),
    Mechanic("trace_path", "annotate", "Draw a trajectory/flow path through space.",
             "ui.trace", ["path"], {"color": P("#54b0ff", "color"), "animated": P(True, "bool")},
             ["auto", "on_step"], "auto"),

    # ── interact ───────────────────────────────────────────────────────────────
    Mechanic("tap_step", "interact", "Tap anywhere to advance to the next beat.",
             "ix.tapstep", [], {}, ["on_tap"], "on_tap"),
    Mechanic("grab_physics", "interact", "Pick up and release with real gravity/collision.",
             "ix.grab", ["physics"], {"mass_kg": P(1.0, "float", [0.05, 50]), "gravity": P(True, "bool")},
             ["on_grab"], "on_grab"),
    Mechanic("rotate_handle", "interact", "Let the user spin the object with a drag.",
             "ix.rotate", [], {"axis": P("y", "axis", AXES + ["free"])}, ["on_grab"], "on_grab"),
    Mechanic("slider_param", "interact", "A slider drives a named animation/param live.",
             "ix.slider", [], {"param": P("angle", "string"), "min": P(0.0, "float"), "max": P(90.0, "float"),
             "bind": P("oscillate.amplitude_deg", "string")}, ["on_slider"], "on_slider"),
    Mechanic("scrub_timeline", "interact", "Scrub a baked clip forward/back by dragging.",
             "ix.scrub", ["animation"], {"clip": P("", "string")}, ["on_slider"], "on_slider"),
    Mechanic("quiz", "interact", "A multiple-choice check tied to the content.",
             "ix.quiz", [], {"question": P("", "string"), "options": P([], "string"), "answer": P(0, "int")},
             ["on_focus", "on_step"], "on_step"),
    Mechanic("toggle_states", "interact", "Tap to cycle an asset through discrete states.",
             "ix.toggle", [], {"states": P([], "string")}, ["on_tap"], "on_tap"),

    # ── camera / stage ─────────────────────────────────────────────────────────
    Mechanic("focus_camera", "camera", "Frame the camera on a target (UX hint, not forced).",
             "cam.focus", [], {"target": P("", "assetRef")}, ["auto", "on_step", "on_focus"], "on_step"),
    Mechanic("orbit_camera", "camera", "Slowly orbit the view around a target.",
             "cam.orbit", [], {"target": P("", "assetRef"), "period_s": P(12.0, "float", [3, 60])},
             ["auto"], "auto"),
    Mechanic("frame_all", "camera", "Pull back to frame the whole scene.",
             "cam.frameall", [], {}, ["auto", "on_step"], "on_step"),

    # ── narrate ────────────────────────────────────────────────────────────────
    Mechanic("speak", "narrate", "Narrate the beat (TTS), optional per platform.",
             "tts.speak", [], {"text": P("", "string")}, ["auto", "on_step"], "auto"),
    Mechanic("caption", "narrate", "Show the beat text as a caption panel.",
             "ui.caption", [], {"text": P("", "string")}, ["auto", "on_step"], "auto"),
]

BY_ID = {m.id: m for m in MECHANICS}
BY_CATEGORY: dict[str, list[str]] = {}
for _m in MECHANICS:
    BY_CATEGORY.setdefault(_m.category, []).append(_m.id)


def usable(mechanic_id: str, caps: set[str]) -> bool:
    """Can this mechanic run given the asset/scene capabilities present?"""
    m = BY_ID.get(mechanic_id)
    if not m:
        return False
    return all((need in caps) for need in m.needs)


def validate_params(mechanic_id: str, params: dict | None) -> dict:
    """Fill defaults + drop unknown keys + clamp ranges. Returns a clean param dict."""
    m = BY_ID.get(mechanic_id)
    if not m:
        return {}
    out = {}
    params = params or {}
    for name, spec in m.params.items():
        val = params.get(name, spec["default"])
        if spec["type"] in ("float", "int") and isinstance(val, (int, float)) and spec.get("range"):
            lo, hi = spec["range"]
            val = max(lo, min(hi, val))
            if spec["type"] == "int":
                val = int(val)
        if spec["type"] == "enum" and spec.get("range") and val not in spec["range"]:
            val = spec["default"]
        if spec["type"] == "axis" and spec.get("range") and val not in spec["range"]:
            val = spec["default"]
        out[name] = val
    return out


def catalog_summary() -> dict:
    return {
        "schemaVersion": "0.5.0-spatail-mechanics",
        "count": len(MECHANICS),
        "categories": {c: ids for c, ids in BY_CATEGORY.items()},
        "capabilities": sorted(CAPABILITIES),
        "triggers": ["auto", "on_tap", "on_focus", "on_step", "on_grab", "on_slider", "timeline"],
        "mechanics": [m.to_dict() for m in MECHANICS],
    }
