"""interaction_orchestrator.py — gate interactions/animations to load phases.

Interactions and animations are triggered by the SPATAIL runtime (never by
Blender), and only AFTER the assets they act on have streamed in. gate() returns,
per interaction, the load phase after which it becomes live; triggers() maps each
interaction's mechanic type to a runtime trigger.
"""
from __future__ import annotations

import experience_spec as es

_TRIGGER = {
    "play_baked": "on_tap",
    "tap_reveal": "on_tap",
    "grab_physics": "on_grab",
    "quiz_panel": "on_focus",
}


class InteractionOrchestrator:
    def gate(self, load_plan, contract: dict) -> dict:
        """interactionId -> {enabledAfterPhase, type, targetAssets}. Interactions
        go live in the final (unlock) phase, once every asset is present."""
        final_phase = load_plan.phases[-1].phase if load_plan.phases else 0
        out = {}
        for it in contract.get("interactions", []):
            out[it["id"]] = {
                "enabledAfterPhase": final_phase,
                "type": it["type"],
                "targetAssets": [a["id"] for a in contract.get("assets", [])],
            }
        return out

    def triggers(self, contract: dict) -> list:
        out = []
        for it in contract.get("interactions", []):
            if it["type"] not in es.MECHANIC_TYPES:
                continue
            out.append({
                "interactionId": it["id"],
                "type": it["type"],
                "trigger": _TRIGGER.get(it["type"], "on_tap"),
            })
        return out
