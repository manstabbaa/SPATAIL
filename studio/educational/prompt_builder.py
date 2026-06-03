"""prompt_builder.py — EducationalPromptBuilder: selected text → a stronger prompt.

Turns a raw selection ("cell division") into a strong SPATAIL prompt plus the
pedagogical metadata the wrapper needs: the preferred representation (from the
reader), a clean classifier prompt, and the engine strategy that representation
maps to. Deterministic; no LLM.
"""
from __future__ import annotations

from . import reader

# Demo-reader representation vocabulary → Representation Engine taxonomy strategy.
EDU_REP_TO_ENGINE = {
    "animated_process": "animated_simulation",
    "flow_visualization": "flow_visualization",
    "cause_effect_visualization": "cause_and_effect",
    "cutaway_view": "cutaway_view",
    "exploded_view": "exploded_view",
    "assembly_sequence": "assembly_sequence",
    "timeline": "timeline",
    "scale_compression": "real_scale_placement",
    "compressed_scale_comparison": "real_scale_placement",
    "guided_tour": "guided_narrative",
    "guided_narrative": "guided_narrative",
    "part_to_whole_explanation": "layered_breakdown",
    "real_scale_preview": "real_scale_placement",
    "interactive_sandbox": "interactive_sandbox",
}


class EducationalPromptBuilder:
    def build(self, selected_text: str, surrounding_context: str = "",
              learning_goal: str | None = None, audience: str = "general learner") -> dict:
        concept = reader.match_concept(selected_text, surrounding_context)
        edu_rep = (concept or {}).get("recommendedRepresentation")
        goal = learning_goal or (concept or {}).get("learningGoal") or f"Explain {selected_text} clearly."
        subject_hint = (concept or {}).get("title") or selected_text
        return {
            "spatailPrompt": self._compose(selected_text, surrounding_context, goal, edu_rep),
            # a concise prompt the deterministic classifier reads cleanly:
            "enginePrompt": f"Explain {subject_hint}",
            "preferredRepresentation": edu_rep or "guided_tour",
            "engineStrategy": EDU_REP_TO_ENGINE.get(edu_rep) if edu_rep else None,
            "learningGoal": goal,
            "targetAudience": audience,
            "conceptId": (concept or {}).get("id"),
        }

    @staticmethod
    def _compose(selected_text: str, context: str, goal: str, edu_rep: str | None) -> str:
        rep = (edu_rep or "guided spatial explanation").replace("_", " ")
        ctx = (context or "").strip()
        ctx_line = (f' Use this surrounding context to identify the key stages: "{ctx[:400]}".'
                    if ctx else "")
        return (f'Create an educational spatial explanation of "{selected_text}". '
                f'Learning goal: {goal} Prefer a {rep} with labeled phases, progressive reveal, '
                f'and interactive controls (step through, tap to highlight, isolate parts, reset).'
                f'{ctx_line}')
