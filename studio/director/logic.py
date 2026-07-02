"""logic.py — the SPATAIL game-manager program (the `logic` section of v0.6).

Turns the composed beats + mechanics + understanding into the interactive layer the
MR runtime's event dispatcher executes — the open-world "the world reacts to you"
behaviour, on top of (not instead of) the linear guided track:

  guidedTrack : the ordered beats (optional linear walkthrough / "main quest")
  triggers    : event -> action rules  (onTap/onApproach/onGaze/onGrab/onQuizCorrect)
  objectives  : the learning goals + their checks
  worldState  : the initial blackboard the manager reads/writes

Pure + deterministic; no device or LLM dependency. The composer can later emit a
richer graph directly; this derives a solid default from whatever mechanics exist.
"""
from __future__ import annotations

# mechanics that read naturally as "tap the thing to make it happen"
_TAP_REVEAL = {"isolate", "zoom_to", "highlight", "explode", "assemble", "cutaway",
               "dissolve", "ghost_others", "before_after", "morph"}


def _triggers_from_beats(beats: list, hero: str) -> list:
    triggers: list = []
    seen = set()

    def add(t: dict):
        key = (t["when"]["event"], t["when"].get("target"),
               tuple(a.get("action") for a in t["do"]))
        if key not in seen:
            seen.add(key)
            triggers.append(t)

    for b in beats or []:
        for mc in b.get("mechanics", []):
            mid = mc.get("mechanic")
            tgt = mc.get("target", "scene")
            if mid == "tap_step":
                add({"when": {"event": "onTap", "target": "scene"},
                     "do": [{"action": "advanceTrack"}]})
            elif mid == "grab_physics":
                add({"when": {"event": "onGrab", "target": tgt},
                     "do": [{"action": "playSound", "params": {"cue": "pickup"}}]})
            elif mid == "toggle_states":
                add({"when": {"event": "onTap", "target": tgt},
                     "do": [{"action": "cycleState", "target": tgt}]})
            elif mid == "quiz":
                add({"when": {"event": "onQuizCorrect", "target": tgt},
                     "do": [{"action": "advanceObjective"},
                            {"action": "playSound", "params": {"cue": "correct"}}]})
            elif mid in _TAP_REVEAL:
                t = tgt if tgt != "scene" else hero
                add({"when": {"event": "onTap", "target": t},
                     "do": [{"action": "mechanic", "mechanic": mid, "target": tgt,
                             "params": mc.get("params", {})}]})

    # always-on emergent reactions so the scene feels alive even with no interaction
    # authored: approaching the hero wakes it; dwelling your gaze plays its motion.
    add({"when": {"event": "onApproach", "target": hero, "params": {"radius_m": 0.7}},
         "do": [{"action": "mechanic", "mechanic": "highlight", "target": hero},
                {"action": "playClipIfAny", "target": hero}]})
    add({"when": {"event": "onGaze", "target": hero, "params": {"dwell_s": 1.0}},
         "do": [{"action": "playClipIfAny", "target": hero},
                {"action": "mechanic", "mechanic": "label", "target": hero}]})
    return triggers


def build_logic(understanding: dict, beats: list, assets: list) -> dict:
    hero = assets[0]["id"] if assets else "scene"
    u = understanding or {}
    objectives = [{
        "id": "understand",
        "goal": u.get("subject", ""),
        "summary": u.get("summary", ""),
        "checks": [{"type": "quiz", "beatId": b.get("id")} for b in (beats or [])
                   if any(mc.get("mechanic") == "quiz" for mc in b.get("mechanics", []))],
    }]
    return {
        "guidedTrack": beats or [],
        "triggers": _triggers_from_beats(beats, hero),
        "objectives": objectives,
        "worldState": {"trackIndex": 0, "discovered": []},
    }


if __name__ == "__main__":
    import json
    beats = [
        {"id": "intro", "mechanics": [{"mechanic": "tap_step"}, {"mechanic": "highlight", "target": "hero"}]},
        {"id": "explore", "mechanics": [{"mechanic": "explode", "target": "hero"},
                                        {"mechanic": "grab_physics", "target": "hero"}]},
        {"id": "check", "mechanics": [{"mechanic": "quiz", "target": "scene"}]},
    ]
    out = build_logic({"subject": "engine", "summary": "How a V8 fires."},
                      beats, [{"id": "hero"}])
    print(json.dumps(out, indent=2))
