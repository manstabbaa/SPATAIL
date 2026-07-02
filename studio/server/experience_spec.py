"""experience_spec.py — the SPATAIL Experience Spec (v0.1): schema, builders, validator.

The Experience Spec is the "post-compile XR" payload (docs/experience_spec_contract.md):
a JSON program the iOS/visionOS app reads live to assemble an interactive education
experience. The Director (server) produces it; the runtime consumes it.

This module is the single Python source of truth: dataclasses that serialise to the
exact JSON shape, plus validate() which enforces every contract rule so the Director
never emits a spec the runtime can't read. Stdlib only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any

SPEC_VERSION = "0.1"

# --- controlled vocabularies (keep in lockstep with the contract + Swift runtime) ---
ANCHORS = {"table", "floor", "free"}
LAYOUTS = {"arc", "line", "cluster"}
FACINGS = {"user", "forward"}
HERO_ANIM = {"baked", "none"}
SCALE_MODES = {"dynamic", "fixed"}
PANEL_KINDS = {"title", "fact", "data", "caption", "quiz"}
PANEL_ANCHORS = {"above_hero", "beside_left", "beside_right", "below_hero"}
PANEL_REVEAL = {"on_focus", "on_tap", "always"}
MECHANIC_TYPES = {"play_baked", "tap_reveal", "grab_physics", "quiz_panel"}
PHYS_BODIES = {"rigid", "bouncy", "heavy", "soft"}
COMFORT_RADIUS_MAX_M = 1.5     # Apple HIG ~1.5 m comfort boundary


class SpecError(ValueError):
    """Raised by validate() when a spec violates the contract."""


# --- dataclasses (serialise to the contract JSON via asdict) -------------------
@dataclass
class Footprint:
    w: float
    d: float
    h: float


@dataclass
class Hero:
    usdz: str
    footprint_m: Footprint
    animation: str = "baked"
    scale_mode: str = "dynamic"


@dataclass
class Panel:
    id: str
    kind: str
    body: str = ""
    title: str = ""
    anchor: str = "above_hero"
    billboard: bool = True
    reveal: str = "on_focus"
    # quiz-only (kind == "quiz"):
    question: str = ""
    options: list[str] = field(default_factory=list)
    answer_index: int | None = None


@dataclass
class Mechanic:
    type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Station:
    id: str
    order: int
    title: str
    hero: Hero
    subtitle: str = ""
    narration: str = ""
    panels: list[Panel] = field(default_factory=list)
    mechanics: list[Mechanic] = field(default_factory=list)


@dataclass
class Placement:
    anchor: str = "table"
    layout: str = "arc"
    comfort_radius_m: float = 1.4
    facing: str = "user"
    spacing_min_m: float = 0.18
    guided: bool = True


@dataclass
class Experience:
    id: str
    title: str
    stations: list[Station]
    prompt: str = ""
    subject: str = "general"
    summary: str = ""
    narration_tone: str = "warm, curious, classroom"
    placement: Placement = field(default_factory=Placement)
    spec_version: str = SPEC_VERSION

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(to_dict(self), indent=indent)


def to_dict(exp: Experience) -> dict:
    """Contract-shaped dict (drops empty quiz fields on non-quiz panels)."""
    d = asdict(exp)
    for st in d["stations"]:
        for p in st["panels"]:
            if p.get("kind") != "quiz":
                for k in ("question", "options", "answer_index"):
                    p.pop(k, None)
            elif p.get("answer_index") is None:
                p.pop("answer_index", None)
    return d


# --- validation ----------------------------------------------------------------
def validate(spec: dict | Experience) -> list[str]:
    """Return [] if the spec satisfies the contract, else a list of problems.
    Accepts either a dict or an Experience (serialised first)."""
    d = to_dict(spec) if isinstance(spec, Experience) else spec
    errs: list[str] = []

    def bad(msg: str) -> None:
        errs.append(msg)

    if not isinstance(d, dict):
        return ["spec is not an object"]

    sv = str(d.get("spec_version", ""))
    if sv.split(".")[0] != SPEC_VERSION.split(".")[0]:
        bad(f"spec_version {sv!r} incompatible with runtime {SPEC_VERSION!r}")
    if not d.get("id"):
        bad("missing id")
    if not d.get("title"):
        bad("missing title")

    pl = d.get("placement", {}) or {}
    if pl.get("anchor", "table") not in ANCHORS:
        bad(f"placement.anchor {pl.get('anchor')!r} not in {sorted(ANCHORS)}")
    if pl.get("layout", "arc") not in LAYOUTS:
        bad(f"placement.layout {pl.get('layout')!r} not in {sorted(LAYOUTS)}")
    if pl.get("facing", "user") not in FACINGS:
        bad(f"placement.facing {pl.get('facing')!r} not in {sorted(FACINGS)}")
    cr = pl.get("comfort_radius_m", 1.4)
    if not isinstance(cr, (int, float)) or not (0 < cr <= COMFORT_RADIUS_MAX_M):
        bad(f"placement.comfort_radius_m {cr!r} must be in (0, {COMFORT_RADIUS_MAX_M}]")

    stations = d.get("stations") or []
    if not stations:
        bad("at least one station required")
    orders: list[int] = []
    for i, st in enumerate(stations):
        tag = f"station[{i}]({st.get('id', '?')})"
        if not st.get("id"):
            bad(f"{tag}: missing id")
        if not st.get("title"):
            bad(f"{tag}: missing title")
        orders.append(st.get("order"))

        hero = st.get("hero") or {}
        if not hero.get("usdz"):
            bad(f"{tag}: hero.usdz missing")
        if hero.get("animation", "baked") not in HERO_ANIM:
            bad(f"{tag}: hero.animation {hero.get('animation')!r} not in {sorted(HERO_ANIM)}")
        if hero.get("scale_mode", "dynamic") not in SCALE_MODES:
            bad(f"{tag}: hero.scale_mode {hero.get('scale_mode')!r} not in {sorted(SCALE_MODES)}")
        fp = hero.get("footprint_m") or {}
        for k in ("w", "d", "h"):
            v = fp.get(k)
            if not isinstance(v, (int, float)) or v <= 0:
                bad(f"{tag}: hero.footprint_m.{k} must be > 0 (got {v!r})")

        panel_ids = {p.get("id") for p in (st.get("panels") or [])}
        for j, p in enumerate(st.get("panels") or []):
            ptag = f"{tag}.panel[{j}]({p.get('id', '?')})"
            if not p.get("id"):
                bad(f"{ptag}: missing id")
            if p.get("kind") not in PANEL_KINDS:
                bad(f"{ptag}: kind {p.get('kind')!r} not in {sorted(PANEL_KINDS)}")
            if p.get("anchor", "above_hero") not in PANEL_ANCHORS:
                bad(f"{ptag}: anchor {p.get('anchor')!r} not in {sorted(PANEL_ANCHORS)}")
            if p.get("reveal", "on_focus") not in PANEL_REVEAL:
                bad(f"{ptag}: reveal {p.get('reveal')!r} not in {sorted(PANEL_REVEAL)}")
            if p.get("kind") == "quiz":
                if not p.get("question"):
                    bad(f"{ptag}: quiz needs a question")
                opts = p.get("options") or []
                if len(opts) < 2:
                    bad(f"{ptag}: quiz needs >=2 options")
                ai = p.get("answer_index")
                if not isinstance(ai, int) or not (0 <= ai < len(opts)):
                    bad(f"{ptag}: quiz answer_index {ai!r} out of range")

        for j, m in enumerate(st.get("mechanics") or []):
            mtag = f"{tag}.mechanic[{j}]({m.get('type', '?')})"
            mt = m.get("type")
            if mt not in MECHANIC_TYPES:
                bad(f"{mtag}: type not in {sorted(MECHANIC_TYPES)}")
                continue
            params = m.get("params") or {}
            if mt == "tap_reveal":
                ref = params.get("reveals")
                if ref and ref not in panel_ids:
                    bad(f"{mtag}: reveals {ref!r} is not a panel id in this station")
            elif mt == "grab_physics":
                body = params.get("body", "rigid")
                if body not in PHYS_BODIES:
                    bad(f"{mtag}: body {body!r} not in {sorted(PHYS_BODIES)}")
            elif mt == "quiz_panel":
                if not params.get("question"):
                    bad(f"{mtag}: quiz_panel needs a question")
                if len(params.get("options") or []) < 2:
                    bad(f"{mtag}: quiz_panel needs >=2 options")
                ai = params.get("answer_index")
                if not isinstance(ai, int) or not (0 <= ai < len(params.get("options") or [])):
                    bad(f"{mtag}: quiz_panel answer_index {ai!r} out of range")

    clean_orders = [o for o in orders if isinstance(o, int)]
    if len(set(clean_orders)) != len(clean_orders):
        bad("station order values must be unique")

    return errs


def validate_or_raise(spec: dict | Experience) -> dict:
    errs = validate(spec)
    if errs:
        raise SpecError("invalid Experience Spec:\n  - " + "\n  - ".join(errs))
    return to_dict(spec) if isinstance(spec, Experience) else spec
