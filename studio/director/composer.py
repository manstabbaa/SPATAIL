"""director.py — the agent that DESIGNS a SPATAIL experience by composing mechanics.

Given understanding (domain/intent/subject/summary) + the available assets and their
capabilities, it freely composes mechanics (studio/mechanics/catalog.py) into ordered
beats. Two composers, same output shape:
  - llm.compose (Gemini)        : an agent picks/params/sequences mechanics with freedom
  - deterministic_compose       : always-on offline fallback (rules by intent/domain)
Everything validates against the catalog, so whatever is chosen the runtime can run it.

A beat = {id, title, narration, focus(assetId), mechanics:[{mechanic,target,params,trigger}]}.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mechanics"))
import catalog as mech  # noqa: E402

_ROT = ("planet", "orbit", "solar", "moon", "gear", "wheel", "spin", "rotat", "turbine", "drum", "fan")
_OSC = ("pendulum", "swing", "wave", "oscill", "vibrat", "metronome", "spring")
_RECIP = ("piston", "plunger", "cylinder")   # whole "engine"/"pump" shouldn't reciprocate as one body


def caps_for(assets: list[dict]) -> set:
    """Capability tokens present across the assets/scene (gates which mechanics apply)."""
    caps = {"none"}
    if len(assets) >= 2:
        caps.add("two_or_more")
    for a in assets:
        if a.get("supportsAnimation"):
            caps.add("animation")
        if a.get("supportsTransparency"):
            caps.add("transparency")
        if a.get("supportsHighlight"):
            caps.add("named_parts")
        if a.get("physics") or a.get("supportsPhysics"):
            caps.add("physics")
        if a.get("path"):
            caps.add("path")
    return caps


def _m(mid: str, target: str = "scene", params: dict | None = None, trigger: str | None = None) -> dict:
    return {"mechanic": mid, "target": target,
            "params": mech.validate_params(mid, params or {}),
            "trigger": trigger or mech.BY_ID[mid].default_trigger}


def validate_beats(beats: list[dict], assets: list[dict], caps: set) -> list[dict]:
    """Keep only catalog-valid, capability-satisfied mechanics with real targets."""
    ids = {a["id"] for a in assets} | {"scene"}
    hero = assets[0]["id"] if assets else "scene"
    clean = []
    for i, b in enumerate(beats):
        mlist = []
        for mc in b.get("mechanics", []):
            mid = mc.get("mechanic")
            if mid not in mech.BY_ID or not mech.usable(mid, caps):
                continue
            tgt = mc.get("target", "scene")
            tgt = tgt if tgt in ids else ("scene" if tgt == "scene" else hero)
            trig = mc.get("trigger") or mech.BY_ID[mid].default_trigger
            if trig not in mech.BY_ID[mid].triggers:
                trig = mech.BY_ID[mid].default_trigger
            mlist.append({"mechanic": mid, "target": tgt,
                          "params": mech.validate_params(mid, mc.get("params")), "trigger": trig})
        focus = b.get("focus")
        clean.append({
            "id": b.get("id") or f"beat_{i}",
            "title": b.get("title", "") or f"Step {i + 1}",
            "narration": b.get("narration", ""),
            "focus": focus if focus in ids else hero,
            "mechanics": mlist,
        })
    return clean


def _hero_motion(subject: str, caps: set, hero: str) -> dict | None:
    s = (subject or "").lower()
    if any(k in s for k in _OSC):
        return _m("oscillate", hero)
    if any(k in s for k in _RECIP):
        return _m("reciprocate", hero)
    if any(k in s for k in _ROT):
        return _m("spin", hero, {"rpm": 8})
    if "animation" in caps:
        return _m("play_clip", hero)
    return _m("spin", hero, {"rpm": 5})


def deterministic_compose(understanding: dict, assets: list[dict], caps: set) -> list[dict]:
    """A solid, varied experience from rules — the offline guarantee."""
    if not assets:
        return [{"id": "intro", "title": understanding.get("subject", "Experience"),
                 "narration": understanding.get("summary", ""),
                 "mechanics": [_m("caption", "scene", {"text": understanding.get("summary", "")})]}]
    hero = assets[0]["id"]
    subject = understanding.get("subject", "")
    intent = (understanding.get("intent") or "explain").lower()
    domain = (understanding.get("domain") or "").lower()
    is_edu = domain in ("educational", "scientific", "biological", "mechanical", "architectural") or True
    beats = []

    # 1) intro — bring it in, name it, narrate
    beats.append({"id": "intro", "title": subject.title() or "Overview",
                  "narration": understanding.get("summary", f"Let's look at {subject}."),
                  "focus": hero, "mechanics": [
                      _m("fade_in", "scene", {"stagger_s": 0.12}),
                      _m("caption", "scene", {"text": understanding.get("summary", "")}),
                      _m("focus_camera", hero),
                      _m("speak", "scene", {"text": understanding.get("summary", "")})]})

    # 2) hero beat — isolate, animate, label
    hero_beat = {"id": "hero", "title": subject.title() or "The main idea",
                 "narration": f"This is {subject}.", "focus": hero, "mechanics": [
                     _m("highlight", hero, {"pulse": True}),
                     _m("label", hero, {"text": (assets[0].get("name") or subject)}),
                     _m("tap_step")]}
    hm = _hero_motion(subject, caps, hero)
    if hm:
        hero_beat["mechanics"].insert(0, hm)
    if "named_parts" in caps and intent in ("assemble", "explore", "explain", "process"):
        hero_beat["mechanics"].insert(1, _m("explode", hero, trigger="on_tap"))
    beats.append(hero_beat)

    # 3) comparison / parts beats per other asset
    others = assets[1:]
    if others and intent in ("compare", "comparison"):
        beats.append({"id": "compare", "title": "Compared", "narration": "Side by side.",
                      "focus": hero, "mechanics": [
                          _m("scale_compare" if "two_or_more" in caps else "side_by_side", "scene"),
                          *[_m("label", a["id"], {"text": a.get("name", a["id"])}) for a in assets]]})
    else:
        for a in others:
            beats.append({"id": f"x_{a['id']}", "title": a.get("name", a["id"]),
                          "narration": a.get("note", f"Here's {a.get('name', a['id'])}."),
                          "focus": a["id"], "mechanics": [
                              _m("focus_camera", a["id"]),
                              _m("ghost_others", a["id"]) if "transparency" in caps else _m("highlight", a["id"]),
                              _m("label", a["id"], {"text": a.get("name", a["id"])}),
                              _m("tap_step")]})

    # 4) recap — frame all, optional quiz
    recap = {"id": "recap", "title": "Recap", "narration": f"That's {subject}.",
             "focus": hero, "mechanics": [_m("frame_all"),
             _m("caption", "scene", {"text": f"{subject.title()} — review."})]}
    if is_edu:
        recap["mechanics"].append(_m("quiz", "scene", {
            "question": f"What did we just explore?", "options": [subject, "Something else"], "answer": 0}))
    beats.append(recap)
    return beats


def compose(understanding: dict, assets: list[dict], *, use_llm: bool = True) -> dict:
    """Design the experience. Tries the LLM agent, validates, falls back to rules."""
    caps = caps_for(assets)
    composer = "deterministic"
    beats = None
    if use_llm:
        try:
            import llm
            raw = llm.compose(understanding, assets, caps)
            beats = validate_beats(raw, assets, caps)
            if beats and any(b["mechanics"] for b in beats):
                composer = "gemini"
            else:
                beats = None
        except Exception as e:  # noqa: BLE001
            print(f"[director] LLM composer unavailable, using rules: {e!r}"[:200])
            beats = None
    if not beats:
        beats = validate_beats(deterministic_compose(understanding, assets, caps), assets, caps)
    mechanics_used = sorted({mc["mechanic"] for b in beats for mc in b["mechanics"]})
    return {"composer": composer, "beats": beats, "mechanicsUsed": mechanics_used,
            "capabilities": sorted(caps)}
