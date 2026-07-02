"""intent_classifier.py — prompt → user intent.

A scored cascade: every intent EXCEPT `preview` competes on signal score; the
highest non-preview intent wins. `preview` ("show me X") is the weak fallback —
it only wins when nothing stronger fires — and `explain` is the ultimate
default. A "three TVs" style numeric-plural boosts `compare`. Optional LLM
refine mirrors the domain classifier.
"""
from __future__ import annotations

from . import taxonomy


class IntentClassifier:
    def __init__(self, use_llm: bool = False):
        self.use_llm = use_llm

    def _score(self, prompt: str) -> dict:
        text = taxonomy.normalize(prompt)
        scores = {}
        for intent, terms in taxonomy.INTENT_SIGNALS.items():
            s = taxonomy.score_terms(text, terms)
            if s:
                scores[intent] = s
        if taxonomy.NUMERIC_PLURAL.search(text):
            scores["compare"] = scores.get("compare", 0) + 2
        return scores

    def classify(self, prompt: str, domain: str | None = None) -> tuple[str, float, str]:
        scores = self._score(prompt)
        strong = {k: v for k, v in scores.items() if k != "preview"}
        if strong:
            intent = max(strong, key=strong.get)
            conf, source = taxonomy.confidence(strong, intent), "rule"
        elif "preview" in scores:
            intent, conf, source = "preview", 0.4, "rule"
        else:
            intent, conf, source = taxonomy.DEFAULT_INTENT, 0.2, "rule"

        if self.use_llm and conf < 0.5:
            refined = self._refine_with_llm(prompt, intent)
            if refined:
                return refined, max(conf, 0.6), "rule+llm"
        return intent, conf, source

    def _refine_with_llm(self, prompt: str, rule_intent: str) -> str | None:
        try:
            import llm_author
            if not llm_author.available():
                return None
            system = (
                "You label a learning prompt with ONE intent from this exact list:\n"
                + ", ".join(taxonomy.INTENTS)
                + '\nReturn ONLY {"intent": "<one of the list>"}.')
            out = llm_author.ask_json(system, f'Prompt: "{prompt}"',
                                      timeout=30.0, max_attempts=1)
            cand = str(out.get("intent", "")).strip().lower()
            return cand if cand in taxonomy.INTENTS else None
        except Exception:
            return None
