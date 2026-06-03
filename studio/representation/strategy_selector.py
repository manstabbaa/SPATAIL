"""strategy_selector.py — (prompt, domain, intent) → presentation strategy.

Deterministic, no LLM. Selection order:
  1. an explicit cue in the prompt (STRATEGY_OVERRIDES) short-circuits, because
     a user who says "exploded view" has already chosen;
  2. otherwise the (domain, intent) entry in STRATEGY_MATRIX;
  3. otherwise the domain's default strategy.

Strategies are flexible spatial-communication approaches, not rigid templates —
the planner is free to compose one with an overlay (e.g. a scientific real-scale
placement gains an orbit animation).
"""
from __future__ import annotations

from . import taxonomy


class StrategySelector:
    def select(self, prompt: str, domain: str, intent: str) -> tuple[str, str]:
        text = taxonomy.normalize(prompt)

        for cue, strategy in taxonomy.STRATEGY_OVERRIDES:
            if cue in text:
                why = (f'explicit cue "{cue.strip()}" in the prompt -> {strategy}. '
                       + taxonomy.STRATEGY_CITATIONS.get(strategy, ""))
                return strategy, why

        strategy = taxonomy.STRATEGY_MATRIX.get((domain, intent))
        if strategy:
            why = (f"{domain} + {intent} -> {strategy}. "
                   + taxonomy.STRATEGY_CITATIONS.get(strategy, ""))
            return strategy, why

        strategy = taxonomy.DOMAIN_DEFAULT_STRATEGY.get(domain, "guided_narrative")
        why = (f"no explicit cue and no {domain}+{intent} rule -> domain default "
               f"{strategy}. " + taxonomy.STRATEGY_CITATIONS.get(strategy, ""))
        return strategy, why
