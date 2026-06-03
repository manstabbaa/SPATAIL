"""domain_classifier.py — prompt → knowledge domain.

Deterministic keyword scoring over taxonomy.DOMAIN_SIGNALS (instant, offline,
testable). An optional `claude`-CLI refine (use_llm=True) can break ties or
correct a weak rule guess; it degrades silently to the rule result if the CLI is
unavailable or returns anything outside the controlled vocabulary.
"""
from __future__ import annotations

from . import taxonomy


class DomainClassifier:
    def __init__(self, use_llm: bool = False):
        self.use_llm = use_llm

    def _score(self, prompt: str) -> dict:
        text = taxonomy.normalize(prompt)
        scores = {}
        for domain, terms in taxonomy.DOMAIN_SIGNALS.items():
            s = taxonomy.score_terms(text, terms)
            if s:
                scores[domain] = s
        return scores

    def classify(self, prompt: str) -> tuple[str, float, str]:
        scores = self._score(prompt)
        if not scores:
            domain, conf, source = taxonomy.DEFAULT_DOMAIN, 0.0, "rule"
        else:
            domain = max(scores, key=scores.get)
            conf = taxonomy.confidence(scores, domain)
            source = "rule"
        if self.use_llm and (conf < 0.5 or not scores):
            refined = self._refine_with_llm(prompt, domain)
            if refined and refined != domain:
                return refined, max(conf, 0.6), "rule+llm"
            if refined:
                return refined, max(conf, 0.6), "rule+llm"
        return domain, conf, source

    def _refine_with_llm(self, prompt: str, rule_domain: str) -> str | None:
        try:
            import llm_author
            if not llm_author.available():
                return None
            system = (
                "You label a learning prompt with ONE domain from this exact list:\n"
                + ", ".join(taxonomy.DOMAINS)
                + '\nReturn ONLY {"domain": "<one of the list>"}.')
            out = llm_author.ask_json(system, f'Prompt: "{prompt}"',
                                      timeout=30.0, max_attempts=1)
            cand = str(out.get("domain", "")).strip().lower()
            return cand if cand in taxonomy.DOMAINS else None
        except Exception:
            return None
