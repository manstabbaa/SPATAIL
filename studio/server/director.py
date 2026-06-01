"""director.py — prompt -> a validated multi-station Experience Spec + N USDZs.

The "brain" of the post-compile XR loop. Two LLM-backed stages (both via the local
`claude` CLI, no API key), then deterministic assembly:

  1. PLAN     - Claude turns the learning prompt into an experience OUTLINE: ordered
                stations, each with a title/subtitle/narration, ONE hero object
                described for Blender, written panels, and interaction mechanics
                chosen from the v0.1 set. Returns strict JSON.
  2. AUTHOR   - for each station, drive the LIVE Blender (clear -> author hero ->
                export USDZ) via generator-style steps, reusing llm_author + the
                exact studio export settings. One USDZ per station.
  3. ASSEMBLE - build an Experience (experience_spec dataclasses) from the plan +
                authored heroes, then validate_or_raise against the contract.

Output: experience.json + <id>_st<N>.usdz files in the artifacts dir. The job
server serves experience.json as experience_url and the USDZs via /artifacts.

Keeps the single-object generator (generator.py) intact for simple prompts; the
job server routes multi-station "experience" jobs here.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_bridge as bridge  # noqa: E402
import llm_author  # noqa: E402
import experience_spec as es  # noqa: E402
import generator  # noqa: E402  (reuse PREPARE + EXPORT Blender code + FPS/FRAMES)

MAX_STATIONS = 5
MIN_STATIONS = 1

_PLAN_SYSTEM = """You are the SPATAIL Director: a curriculum + spatial-experience \
designer. You turn a learning prompt into a SHORT, vivid, GAME-LIKE education \
experience to be built in the learner's real room as mixed-reality XR. You design \
STRUCTURE only — geometry is authored separately in Blender.

Design principles:
- A few ordered STATIONS (learning points), like a guided presentation the learner \
walks between. Aim for 3 stations (min %(MIN)d, max %(MAX)d). Build understanding \
in order; spark inquiry (a hook question, a prediction, a reveal).
- For EACH station decide what is better SHOWN vs WRITTEN vs FELT:
    * better seen  -> the HERO 3D object + its baked animation (describe it for Blender)
    * better written -> PANELS (short spatial-UI cards: a definition, a number, a caption)
    * better felt  -> MECHANICS (interaction). Use ONLY these v0.1 types:
        - play_baked   : play the hero's baked animation        params: {trigger: on_focus|on_tap|auto, loop: bool}
        - tap_reveal   : tap hero to reveal a panel             params: {target:"hero", reveals:"<panelId in same station>"}
        - grab_physics : pick up & toss the hero with physics   params: {body: rigid|bouncy|heavy|soft}
        - quiz_panel   : a question that checks understanding   params: {question, options:[..], answer_index, on_correct: advance|celebrate}
- Bias toward SPATIAL + INTERACTIVE (it's the selling point), but keep heavy text in \
panels, not narration. Keep each hero a single tabletop object (<= ~0.9 m).

Return ONLY a single JSON object with this exact shape (no prose, no fences):
{
  "title": str, "subject": str, "summary": str (one line),
  "narration_tone": str,
  "placement": {"anchor": "table"|"floor"|"free", "layout": "arc"|"line"|"cluster"},
  "stations": [
    {
      "title": str, "subtitle": str, "narration": str (1-2 sentences, spoken),
      "hero_prompt": str,   // a concrete description for Blender: object + the action to animate
      "panels": [ {"kind": "title"|"fact"|"data"|"caption"|"quiz",
                   "title": str, "body": str,
                   "anchor": "above_hero"|"beside_left"|"beside_right"|"below_hero",
                   // for kind=="quiz" also: "question": str, "options": [str,..], "answer_index": int
                  } ],
      "mechanics": [ {"type": "...", "params": { ... }} ]
    }
  ]
}
""" % {"MIN": MIN_STATIONS, "MAX": MAX_STATIONS}


def _plan(prompt: str) -> dict:
    user = (f'Learning prompt: "{prompt}"\n'
            "Design the experience now. Return only the JSON object.")
    plan = llm_author.ask_json(_PLAN_SYSTEM, user, timeout=180.0)
    stations = plan.get("stations") or []
    if not stations:
        raise RuntimeError("Director plan had no stations")
    # clamp station count
    plan["stations"] = stations[:MAX_STATIONS]
    return plan


def _author_hero(hero_prompt: str, usdz_path: str, on_stage) -> dict:
    """Clear the live scene, author one hero from hero_prompt, export its USDZ.
    Reuses generator's PREPARE + EXPORT Blender code (exact studio settings)."""
    bridge.run_code(generator._PREPARE, timeout=60.0)
    llm_author.author_scene(hero_prompt, generator.FRAMES, generator.FPS,
                            bridge.run_code, on_stage=on_stage)
    export_code = generator._EXPORT.replace("{USDZ}", usdz_path.replace("\\", "/"))
    res = bridge.run_code(export_code, timeout=180.0)
    if not res.get("exported"):
        raise RuntimeError(f"hero USDZ export failed: {res.get('error')}")
    return res


def _mk_panels(raw_panels: list[dict], st_idx: int) -> list[es.Panel]:
    panels = []
    for k, p in enumerate(raw_panels or []):
        kind = p.get("kind", "fact")
        kind = kind if kind in es.PANEL_KINDS else "fact"
        anchor = p.get("anchor", "above_hero")
        anchor = anchor if anchor in es.PANEL_ANCHORS else "above_hero"
        panel = es.Panel(
            id=f"st{st_idx}_p{k+1}", kind=kind,
            title=str(p.get("title", ""))[:80], body=str(p.get("body", ""))[:400],
            anchor=anchor, reveal=p.get("reveal", "on_focus")
            if p.get("reveal") in es.PANEL_REVEAL else "on_focus",
        )
        if kind == "quiz":
            panel.question = str(p.get("question", p.get("title", "")))[:200]
            panel.options = [str(o)[:80] for o in (p.get("options") or [])][:4]
            ai = p.get("answer_index", 0)
            panel.answer_index = ai if isinstance(ai, int) and 0 <= ai < len(panel.options) else 0
        panels.append(panel)
    return panels


def _mk_mechanics(raw_mechs: list[dict], panel_ids: set[str]) -> list[es.Mechanic]:
    mechs = []
    for m in raw_mechs or []:
        mt = m.get("type")
        if mt not in es.MECHANIC_TYPES:
            continue
        params = dict(m.get("params") or {})
        if mt == "tap_reveal":
            # only keep a reveals ref if it points at a real panel in this station
            if params.get("reveals") not in panel_ids:
                params.pop("reveals", None)
            params.setdefault("target", "hero")
        elif mt == "grab_physics":
            if params.get("body") not in es.PHYS_BODIES:
                params["body"] = "rigid"
        elif mt == "quiz_panel":
            opts = [str(o)[:80] for o in (params.get("options") or [])][:4]
            if len(opts) < 2:
                continue  # invalid quiz mechanic; drop it
            params["options"] = opts
            ai = params.get("answer_index", 0)
            params["answer_index"] = ai if isinstance(ai, int) and 0 <= ai < len(opts) else 0
            params.setdefault("question", "")
            params.setdefault("on_correct", "advance")
        mechs.append(es.Mechanic(mt, params))
    return mechs


def generate_experience(prompt: str, exp_id: str, out_dir,
                        on_stage=lambda s: None) -> dict:
    """Full Director run. Returns artifact info; raises on failure (no fallback)."""
    if bridge.ping() is None:
        raise RuntimeError("Blender MCP bridge not reachable on localhost:9876")
    if not llm_author.available():
        raise RuntimeError("claude CLI not available for the Director")

    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    on_stage("planning")
    plan = _plan(prompt)
    raw_stations = plan["stations"]
    n = len(raw_stations)

    pl_raw = plan.get("placement") or {}
    placement = es.Placement(
        anchor=pl_raw.get("anchor", "table") if pl_raw.get("anchor") in es.ANCHORS else "table",
        layout=pl_raw.get("layout", "arc") if pl_raw.get("layout") in es.LAYOUTS else "arc",
    )

    stations: list[es.Station] = []
    for i, rs in enumerate(raw_stations, start=1):
        on_stage(f"authoring station {i}/{n}")
        usdz_name = f"{exp_id}_st{i}.usdz"
        usdz_path = str(out_dir / usdz_name)
        hero_prompt = rs.get("hero_prompt") or rs.get("title") or prompt
        res = _author_hero(hero_prompt, usdz_path,
                           on_stage=lambda s, i=i, n=n: on_stage(f"station {i}/{n}: {s}"))

        # real footprint from the authored bbox (Y-up metres)
        bb = res.get("bbox_yup", {"min": [0, 0, 0], "max": [0.3, 0.3, 0.3]})
        mn, mx = bb["min"], bb["max"]
        fp = es.Footprint(w=max(0.05, round(mx[0] - mn[0], 4)),
                          d=max(0.05, round(mx[2] - mn[2], 4)),
                          h=max(0.05, round(mx[1] - mn[1], 4)))

        panels = _mk_panels(rs.get("panels"), i)
        panel_ids = {p.id for p in panels}
        mechanics = _mk_mechanics(rs.get("mechanics"), panel_ids)
        # guarantee at least one mechanic so a station is never inert: play the baked anim
        if not mechanics:
            mechanics = [es.Mechanic("play_baked", {"trigger": "on_focus", "loop": True})]

        stations.append(es.Station(
            id=f"st{i}", order=i,
            title=str(rs.get("title", f"Station {i}"))[:120],
            subtitle=str(rs.get("subtitle", ""))[:120],
            narration=str(rs.get("narration", ""))[:400],
            hero=es.Hero(usdz=usdz_name, footprint_m=fp,
                         animation="baked", scale_mode="dynamic"),
            panels=panels, mechanics=mechanics,
        ))

    on_stage("assembling")
    exp = es.Experience(
        id=exp_id,
        title=str(plan.get("title", prompt))[:120],
        subject=str(plan.get("subject", "general"))[:40],
        prompt=prompt,
        summary=str(plan.get("summary", ""))[:200],
        narration_tone=str(plan.get("narration_tone", "warm, curious, classroom"))[:80],
        placement=placement,
        stations=stations,
    )
    spec = es.validate_or_raise(exp)   # raises SpecError if the Director drifted

    exp_path = out_dir / f"{exp_id}.json"
    exp_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    on_stage("ready")

    return {
        "experience_name": exp_path.name,
        "experience_url": f"/artifacts/{exp_path.name}",
        "usdz_names": [s.hero.usdz for s in stations],
        "stations": len(stations),
        "title": exp.title,
    }
