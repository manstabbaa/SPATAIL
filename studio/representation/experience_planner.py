"""experience_planner.py — prompt → ExperiencePlan.

Runs the classifiers, picks a strategy, then assembles the full Experience Plan:
subject, placement (es vocab), experience beats (from the strategy template),
required assets (a hero + named parts / comparison items), required animations
(motions the factory/runtime can drive), and interactions (es.MECHANIC_TYPES).

Pure Python: no Blender, no network unless use_llm=True. This is what guarantees
the <1 s progressive phase-0 can be produced from the plan alone.
"""
from __future__ import annotations

import re

from . import taxonomy
from .domain_classifier import DomainClassifier
from .intent_classifier import IntentClassifier
from .strategy_selector import StrategySelector
from .types import (ExperienceBeat, ExperiencePlan, InteractionSpec, Placement,
                    RequiredAnimation, RequiredAsset)

_WORD_RE = re.compile(r"[^a-z0-9]+")
_COUNT_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10}

_PLACE_CLAUSE = re.compile(
    r"\b(on|in|onto|against|over|across)\s+(my|the|your|a|an)\s+"
    r"(table|desk|tabletop|counter|wall|floor|ground|room|space|hand|palm|ceiling)\b.*$",
    re.I)
_HOW_BUILT = re.compile(
    r"\bhow\s+(?:a|an|the)?\s*(.+?)\s+(?:is|are|gets?)\s+"
    r"(?:assembled|built|made|constructed|put\s+together)\b", re.I)
_LEAD_VERBS = re.compile(
    r"^\s*(?:please\s+)?(?:can you\s+)?(?:let me\s+)?(?:i\s+want\s+to\s+)?"
    r"(?:explain|show me|show|tell me about|describe|teach me|teach|compare|"
    r"demonstrate|simulate|preview|analyze|analyse|explore|present|build|"
    r"assemble|visualize|visualise|see)\b[\s:,-]*", re.I)
_LEAD_HOW = re.compile(r"^\s*how\s+(?:does|do)\s+(?:a|an|the)?\s*", re.I)
_LEAD_ARTICLE = re.compile(r"^\s*(?:a|an|the)\s+", re.I)
_LEAD_COUNT = re.compile(
    r"^\s*(?:two|three|four|five|six|seven|eight|nine|ten|several|multiple|many|\d+)\s+",
    re.I)


def _slug(s: str) -> str:
    return _WORD_RE.sub("_", (s or "").lower()).strip("_") or "subject"


def _singular(s: str) -> str:
    s = s.strip()
    return s[:-1] if len(s) > 3 and s.lower().endswith("s") else s


def _subject_of(prompt: str) -> str:
    s = (prompt or "").strip()
    m = _HOW_BUILT.search(s)
    if m:
        s = m.group(1)
    else:
        s = _PLACE_CLAUSE.sub("", s).strip()
        s = _LEAD_VERBS.sub("", s).strip()
        s = _LEAD_HOW.sub("", s).strip()
        s = _LEAD_ARTICLE.sub("", s).strip()
    s = _LEAD_COUNT.sub("", s).strip()
    s = _LEAD_ARTICLE.sub("", s).strip()
    s = re.sub(r"[?.!]+$", "", s).strip()
    return s or (prompt or "").strip()


def _compare_count(prompt: str, default: int = 3) -> int:
    # Word counts beat digits ("v8" must never be read as a count); whole-word only.
    low = taxonomy.normalize(prompt)
    for word, n in _COUNT_WORDS.items():
        if re.search(r"\b" + word + r"\b", low):
            return n
    m = re.search(r"\b([2-9])\b", low)
    return int(m.group(1)) if m else default


def _placement_of(prompt: str, domain: str) -> Placement:
    base = dict(taxonomy.PLACEMENT_BY_DOMAIN.get(
        domain, taxonomy.PLACEMENT_BY_DOMAIN["general_knowledge"]))
    low = " " + (prompt or "").lower() + " "
    for clause, anchor in taxonomy.CLAUSE_TO_ANCHOR.items():
        if re.search(r"\b(?:on|in|onto|against|over|across)\s+(?:my|the|your|a|an)\s+"
                     + clause + r"\b", low):
            base["anchor"] = anchor
            break
    return Placement(anchor=base["anchor"], layout=base["layout"],
                     facing=base["facing"], scale_mode=base["scale_mode"])


def _beats_for(strategy: str, subject: str, hero_id: str) -> list[ExperienceBeat]:
    tmpl = taxonomy.BEAT_TEMPLATES.get(strategy) or taxonomy.BEAT_TEMPLATES["guided_narrative"]
    beats = []
    for bid, title, narration, focus, reveal in tmpl:
        beats.append(ExperienceBeat(
            id=bid,
            title=title.format(subject=subject),
            narration=narration.format(subject=subject),
            focus=hero_id if focus == "hero" else focus,
            reveal=reveal,
        ))
    return beats


def _assets_for(strategy: str, domain: str, subject: str, hero_id: str,
                n_compare: int) -> list[RequiredAsset]:
    if strategy == "comparison_layout":
        base = _slug(_singular(subject)) or "item"
        return [RequiredAsset(id=f"{base}_{i + 1}", type="comparison_item",
                              priority=0 if i == 0 else 1) for i in range(n_compare)]

    assets = [RequiredAsset(id=hero_id, type="hero", priority=0)]
    if strategy in ("exploded_view", "cutaway_view", "layered_breakdown"):
        assets += [RequiredAsset(id=f"{hero_id}_part_a", type="part", priority=1),
                   RequiredAsset(id=f"{hero_id}_part_b", type="part", priority=1),
                   RequiredAsset(id=f"{hero_id}_core", type="part", priority=2)]
    elif strategy == "assembly_sequence":
        assets += [RequiredAsset(id=f"{hero_id}_base", type="part", priority=1),
                   RequiredAsset(id=f"{hero_id}_mid", type="part", priority=1),
                   RequiredAsset(id=f"{hero_id}_top", type="part", priority=2)]
    elif strategy == "animated_simulation":
        assets += [RequiredAsset(id=f"{hero_id}_mover", type="part", priority=1)]
    elif strategy == "real_scale_placement":
        if domain == "scientific":
            assets += [RequiredAsset(id=f"{hero_id}_orbiter_a", type="part", priority=1),
                       RequiredAsset(id=f"{hero_id}_orbiter_b", type="part", priority=2)]
        else:
            assets += [RequiredAsset(id=f"{hero_id}_detail", type="part", priority=2)]
    else:  # timeline / flow / cause_and_effect / guided_narrative / sandbox
        assets += [RequiredAsset(id=f"{hero_id}_context", type="environment", priority=2)]
    return assets


def _animations_for(strategy: str, domain: str,
                    assets: list[RequiredAsset]) -> list[RequiredAnimation]:
    if not assets:
        return []
    hero = assets[0].id
    parts = [a for a in assets if a.type == "part"]
    anims: list[RequiredAnimation] = []
    if strategy == "exploded_view":
        anims.append(RequiredAnimation(f"{hero}_assemble", hero, "assemble",
                                       "explode / collapse tween over per-part offsets"))
        if domain == "mechanical" and parts:
            anims.append(RequiredAnimation(f"{parts[0].id}_recip", parts[0].id,
                                           "reciprocate", "piston-like internal motion"))
    elif strategy == "assembly_sequence":
        anims.append(RequiredAnimation(f"{hero}_assemble", hero, "assemble",
                                       "per-step seat-into-place tween"))
    elif strategy == "animated_simulation":
        anims.append(RequiredAnimation(f"{hero}_sim", hero, "spin",
                                       "seamless looping process"))
    elif strategy == "real_scale_placement" and domain == "scientific":
        for a in parts:
            anims.append(RequiredAnimation(f"{a.id}_orbit", a.id, "orbit",
                                           "orbital motion overlay"))
    # comparison_layout / timeline / cutaway_view etc. are static or camera-driven.
    return anims


def _interactions_for(strategy: str, subject: str) -> list[InteractionSpec]:
    play = lambda i, lbl, **p: InteractionSpec(i, "play_baked", lbl, p)
    tap = lambda i, lbl, **p: InteractionSpec(i, "tap_reveal", lbl, p)
    grab = lambda i, lbl: InteractionSpec(i, "grab_physics", lbl, {"body": "rigid"})
    quiz = lambda: InteractionSpec(
        "check_understanding", "quiz_panel", "Check understanding",
        {"question": f"What did you notice about the {subject}?",
         "options": ["It has distinct parts", "It moves as one piece", "Not sure"],
         "answer_index": 0})

    if strategy == "exploded_view":
        return [play("explode_toggle", "Explode / collapse", loop=False),
                tap("isolate_part", "Tap a part to isolate it"),
                grab("handle_part", "Pick up a part")]
    if strategy == "cutaway_view":
        return [play("cut_toggle", "Open / close the cutaway", loop=False),
                tap("label_interior", "Tap to label an interior part")]
    if strategy == "animated_simulation":
        return [play("play_pause", "Play / pause", loop=True),
                tap("highlight_phase", "Tap to highlight this phase")]
    if strategy == "assembly_sequence":
        return [play("step_through", "Step through assembly", loop=False),
                tap("highlight_part", "Tap the active part")]
    if strategy == "comparison_layout":
        return [tap("show_spec", "Tap to show specs"), quiz()]
    if strategy == "real_scale_placement":
        return [play("orbit_toggle", "Play / pause motion", loop=True),
                tap("label_part", "Tap to label"), grab("reposition", "Move it")]
    if strategy == "timeline":
        return [play("scrub_timeline", "Scrub the timeline", loop=False),
                tap("expand_event", "Tap to expand an event")]
    return [tap("reveal", "Tap to reveal more"),
            play("play_pause", "Play / pause", loop=True)]


class ExperiencePlanner:
    def __init__(self, use_llm: bool = False):
        self.use_llm = use_llm
        self._domain = DomainClassifier(use_llm=use_llm)
        self._intent = IntentClassifier(use_llm=use_llm)
        self._strategy = StrategySelector()

    def plan(self, prompt: str, *, experience_id: str | None = None,
             strategy_override: str | None = None) -> ExperiencePlan:
        domain, dconf, dsrc = self._domain.classify(prompt)
        intent, iconf, isrc = self._intent.classify(prompt, domain)
        if strategy_override and strategy_override in taxonomy.STRATEGIES:
            strategy = strategy_override
            reasoning = (f"caller override -> {strategy}. "
                         + taxonomy.STRATEGY_CITATIONS.get(strategy, ""))
        else:
            strategy, reasoning = self._strategy.select(prompt, domain, intent)

        subject = _subject_of(prompt)
        placement = _placement_of(prompt, domain)
        exp_id = experience_id or _slug(subject)
        hero_id = _slug(subject)
        n_compare = _compare_count(prompt) if strategy == "comparison_layout" else 0

        assets = _assets_for(strategy, domain, subject, hero_id, n_compare)
        beats = _beats_for(strategy, subject, assets[0].id if assets else hero_id)
        anims = _animations_for(strategy, domain, assets)
        interactions = _interactions_for(strategy, subject)
        source = "rule+llm" if "llm" in (dsrc + isrc) else "rule"

        return ExperiencePlan(
            experienceId=exp_id, domain=domain, intent=intent, strategy=strategy,
            subject=subject, placement=placement,
            domainConfidence=dconf, intentConfidence=iconf, source=source,
            reasoning=reasoning, experienceBeats=beats, requiredAssets=assets,
            requiredAnimations=anims, interactions=interactions,
        )
