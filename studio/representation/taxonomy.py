"""taxonomy.py — the brain's knowledge base (a.k.a. SpatialPresentationPrinciples).

One data-driven place for every controlled vocabulary and every heuristic the
classifiers use, so the decision "what is the best way for a human to understand
this concept spatially?" is inspectable and testable rather than buried in code.

The lists below are deliberately FLEXIBLE strategies, not rigid templates: a
prompt can pick a strategy off the matrix, override it with an explicit cue, and
the planner is free to compose (e.g. real_scale_placement + an orbit overlay).

Placement values are kept inside the studio Experience Spec vocab
(experience_spec.py) so a plan can be lowered into a valid es.Experience later.
"""
from __future__ import annotations

import re

# ── Controlled vocabularies (the task's taxonomies) ──────────────────────────
DOMAINS = [
    "mechanical", "biological", "scientific", "architectural", "historical",
    "product", "manufacturing", "data_visualization", "educational",
    "general_knowledge",
]
INTENTS = [
    "explain", "learn", "compare", "preview", "assemble", "demonstrate",
    "analyze", "explore", "present",
]
STRATEGIES = [
    "real_scale_placement", "exploded_view", "cutaway_view",
    "animated_simulation", "assembly_sequence", "timeline", "guided_narrative",
    "comparison_layout", "layered_breakdown", "flow_visualization",
    "cause_and_effect", "interactive_sandbox",
]

DEFAULT_DOMAIN = "general_knowledge"
DEFAULT_INTENT = "explain"

# ── Keyword signal tables (multi-word terms score 2, single words score 1) ───
# Domain cues are NOUNS about the subject. Verbs of intent ("assemble") live in
# INTENT_SIGNALS, not here, so "how a bridge is assembled" stays architectural.
DOMAIN_SIGNALS = {
    "mechanical": [
        "engine", "v8", "v6", "v12", "motor", "piston", "gear", "crankshaft",
        "camshaft", "turbine", "machine", "mechanism", "transmission",
        "hydraulic", "pump", "valve", "bearing", "gearbox", "clutch",
    ],
    "biological": [
        "cell", "division", "mitosis", "meiosis", "dna", "organ", "heart",
        "anatomy", "neuron", "photosynthesis", "organism", "muscle", "bacteria",
        "virus", "protein", "enzyme", "blood", "lung", "brain", "tissue",
    ],
    "scientific": [
        "solar system", "planet", "atom", "molecule", "gravity", "orbit",
        "physics", "chemistry", "electron", "galaxy", "reaction", "magnetic",
        "wave", "particle", "star", "sun", "nucleus", "energy", "quantum",
    ],
    "architectural": [
        "building", "bridge", "house", "structure", "beam", "truss", "arch",
        "skyscraper", "floor plan", "column", "foundation", "roof", "dome",
        "tower", "facade", "cantilever",
    ],
    "historical": [
        "history", "ancient", "war", "empire", "century", "era", "dynasty",
        "revolution", "medieval", "civilization", "battle", "historical",
        "timeline of", "evolution of",
    ],
    "product": [
        "tv", "tvs", "television", "televisions", "phone", "smartphone",
        "laptop", "sofa", "chair", "gadget", "device", "headphones", "watch",
        "appliance", "camera", "speaker", "monitor", "drone", "console",
    ],
    "manufacturing": [
        "factory", "weld", "welding", "machined", "production line",
        "fabrication", "cnc", "supply chain", "manufacturing", "injection molding",
        "conveyor", "assembly line", "tooling",
    ],
    "data_visualization": [
        "chart", "graph", "data", "dataset", "statistics", "trend", "metric",
        "metrics", "dashboard", "distribution", "kpi", "analytics", "histogram",
        "scatter", "correlation",
    ],
    # Weak, generic cues — used only as a faint tiebreaker, never to outvote a
    # concrete subject domain.
    "educational": ["lesson", "classroom", "curriculum", "concept", "tutorial"],
    "general_knowledge": [],
}

# Intent cues. `preview` is the WEAK fallback ("show me X"): it only wins when no
# stronger intent fires. `demonstrate` keys on dynamic-process words (a thing to
# be shown in motion) as well as verbs.
INTENT_SIGNALS = {
    "explain": [
        "explain", "how does", "how do", "what is", "what are", "why",
        "understand", "tell me about", "describe",
    ],
    "learn": ["learn", "teach me", "teach", "study", "memorize", "quiz me"],
    "compare": [
        "compare", "comparison", "versus", "vs", "difference between",
        "differences", "which is better", "side by side", "side-by-side",
    ],
    "assemble": [
        "assemble", "assembled", "assembly", "build", "put together", "install",
        "installation", "set up", "construct", "how to build", "is built",
    ],
    "demonstrate": [
        "demonstrate", "simulate", "simulation", "in action", "in motion",
        "working", "how it works", "division", "dividing", "rotate", "rotating",
        "spin", "spinning", "orbit", "orbiting", "flow", "flowing", "cycle",
        "beating", "moving", "firing", "combustion",
    ],
    "analyze": [
        "analyze", "analyse", "break down", "breakdown", "inspect", "examine",
        "diagnose", "investigate", "audit",
    ],
    "explore": ["explore", "walk through", "walkthrough", "tour", "browse", "wander"],
    "present": ["present", "presentation", "pitch", "showcase", "show off"],
    "preview": ["show me", "show", "view", "look at", "preview", "see", "display", "render"],
}

# "three TVs", "2 phones" → a comparison cue independent of the word "compare".
NUMERIC_PLURAL = re.compile(
    r"\b(two|three|four|five|six|seven|eight|nine|ten|several|multiple|many|[2-9]|\d{2,})\b\s+\w+s\b")

# ── Strategy selection ───────────────────────────────────────────────────────
# Explicit cues short-circuit the matrix (checked in order; first hit wins).
STRATEGY_OVERRIDES = [
    ("exploded", "exploded_view"),
    ("explode", "exploded_view"),
    ("cutaway", "cutaway_view"),
    ("cross section", "cutaway_view"),
    ("cross-section", "cutaway_view"),
    ("section view", "cutaway_view"),
    ("timeline", "timeline"),
    ("history of", "timeline"),
    ("over time", "timeline"),
    ("evolution of", "timeline"),
    ("side by side", "comparison_layout"),
    ("compare", "comparison_layout"),
    ("versus", "comparison_layout"),
    (" vs ", "comparison_layout"),
    ("assemble", "assembly_sequence"),
    ("assembled", "assembly_sequence"),
    ("assembly", "assembly_sequence"),
    ("put together", "assembly_sequence"),
    ("simulate", "animated_simulation"),
    ("simulation", "animated_simulation"),
    ("in action", "animated_simulation"),
    ("sandbox", "interactive_sandbox"),
    ("play with", "interactive_sandbox"),
    ("experiment with", "interactive_sandbox"),
    ("data flow", "flow_visualization"),
    ("pipeline", "flow_visualization"),
    ("cause and effect", "cause_and_effect"),
    ("layer by layer", "layered_breakdown"),
]

# (domain, intent) → strategy. Misses fall back to DOMAIN_DEFAULT_STRATEGY.
STRATEGY_MATRIX = {
    ("mechanical", "explain"): "exploded_view",
    ("mechanical", "demonstrate"): "animated_simulation",
    ("mechanical", "analyze"): "cutaway_view",
    ("mechanical", "assemble"): "assembly_sequence",
    ("mechanical", "learn"): "layered_breakdown",
    ("mechanical", "explore"): "interactive_sandbox",
    ("mechanical", "present"): "guided_narrative",
    ("mechanical", "compare"): "comparison_layout",

    ("biological", "explain"): "layered_breakdown",
    ("biological", "demonstrate"): "animated_simulation",
    ("biological", "analyze"): "cutaway_view",
    ("biological", "learn"): "guided_narrative",
    ("biological", "explore"): "interactive_sandbox",

    ("scientific", "explain"): "real_scale_placement",
    ("scientific", "demonstrate"): "animated_simulation",
    ("scientific", "analyze"): "cutaway_view",
    ("scientific", "explore"): "interactive_sandbox",
    ("scientific", "learn"): "guided_narrative",

    ("architectural", "assemble"): "assembly_sequence",
    ("architectural", "explain"): "cutaway_view",
    ("architectural", "preview"): "real_scale_placement",
    ("architectural", "present"): "guided_narrative",
    ("architectural", "explore"): "interactive_sandbox",
    ("architectural", "analyze"): "layered_breakdown",

    ("manufacturing", "assemble"): "assembly_sequence",
    ("manufacturing", "demonstrate"): "flow_visualization",
    ("manufacturing", "analyze"): "flow_visualization",
    ("manufacturing", "explain"): "assembly_sequence",

    ("product", "compare"): "comparison_layout",
    ("product", "preview"): "real_scale_placement",
    ("product", "present"): "guided_narrative",
    ("product", "explain"): "exploded_view",
    ("product", "analyze"): "cutaway_view",

    ("historical", "explain"): "timeline",
    ("historical", "learn"): "timeline",
    ("historical", "present"): "timeline",
    ("historical", "explore"): "timeline",
    ("historical", "demonstrate"): "animated_simulation",

    ("data_visualization", "compare"): "comparison_layout",
    ("data_visualization", "analyze"): "flow_visualization",
    ("data_visualization", "explain"): "flow_visualization",
    ("data_visualization", "present"): "flow_visualization",
}

DOMAIN_DEFAULT_STRATEGY = {
    "mechanical": "exploded_view",
    "biological": "animated_simulation",
    "scientific": "real_scale_placement",
    "architectural": "real_scale_placement",
    "historical": "timeline",
    "product": "real_scale_placement",
    "manufacturing": "assembly_sequence",
    "data_visualization": "flow_visualization",
    "educational": "guided_narrative",
    "general_knowledge": "guided_narrative",
}

# ── Placement defaults per domain (es vocab) ─────────────────────────────────
# anchor ∈ {table, floor, free} · layout ∈ {arc, line, cluster}
# facing ∈ {user, forward} · scale_mode ∈ {dynamic, fixed}
PLACEMENT_BY_DOMAIN = {
    "mechanical":         {"anchor": "table", "layout": "cluster", "facing": "user", "scale_mode": "dynamic"},
    "biological":         {"anchor": "table", "layout": "cluster", "facing": "user", "scale_mode": "dynamic"},
    "scientific":         {"anchor": "floor", "layout": "cluster", "facing": "user", "scale_mode": "dynamic"},
    "architectural":      {"anchor": "floor", "layout": "line",    "facing": "user", "scale_mode": "dynamic"},
    "historical":         {"anchor": "free",  "layout": "line",    "facing": "user", "scale_mode": "fixed"},
    "product":            {"anchor": "free",  "layout": "arc",     "facing": "user", "scale_mode": "fixed"},
    "manufacturing":      {"anchor": "floor", "layout": "line",    "facing": "user", "scale_mode": "dynamic"},
    "data_visualization": {"anchor": "free",  "layout": "arc",     "facing": "user", "scale_mode": "fixed"},
    "educational":        {"anchor": "table", "layout": "arc",     "facing": "user", "scale_mode": "dynamic"},
    "general_knowledge":  {"anchor": "table", "layout": "arc",     "facing": "user", "scale_mode": "dynamic"},
}

# A spatial clause in the prompt ("on my wall") overrides the domain anchor.
# es has no "wall" anchor, so a wall maps to a free-floating vertical surface.
CLAUSE_TO_ANCHOR = {
    "table": "table", "desk": "table", "tabletop": "table", "counter": "table",
    "hand": "table", "palm": "table",
    "wall": "free", "ceiling": "free",
    "floor": "floor", "ground": "floor", "room": "floor", "space": "floor",
}

# ── Why each strategy (the spatial-communication rationale) ──────────────────
STRATEGY_CITATIONS = {
    "real_scale_placement": "Place the subject at true scale in the room so the body reads proportion and presence directly — best when size and context ARE the lesson.",
    "exploded_view": "Separate parts along their assembly axes to reveal internal structure and how components relate — best when understanding lives in the parts.",
    "cutaway_view": "Slice the outer shell away to expose interior anatomy in situ — best when the inside matters but the parts shouldn't leave home.",
    "animated_simulation": "Show the process in motion as a seamless loop — best when the concept is a behaviour over time, not a static object.",
    "assembly_sequence": "Build the subject up step by step in space so order and fit are felt — best for how-it-goes-together tasks.",
    "timeline": "Lay events along a spatial line the user walks — best when the concept is a sequence through time.",
    "guided_narrative": "Walk the user through staged beats with narration and attention cues — best when a story/order carries the meaning.",
    "comparison_layout": "Place options side by side at equal distance on a comfort arc — best when the goal is to choose between alternatives.",
    "layered_breakdown": "Peel the subject into stacked semantic layers the user reveals one at a time — best for nested structure.",
    "flow_visualization": "Visualise movement/quantity flowing through a system — best for processes, pipelines, and data movement.",
    "cause_and_effect": "Stage a trigger and its consequence so the link is seen, not told — best for mechanisms of influence.",
    "interactive_sandbox": "Give the user direct controls to manipulate the subject and observe outcomes — best when discovery beats narration.",
}

# ── Per-strategy beat templates (the planner fills {subject}) ────────────────
# Each beat: (id, title, narration, focus, reveal). `focus` "hero" = the primary
# asset; the planner remaps it onto a concrete asset id.
BEAT_TEMPLATES = {
    "exploded_view": [
        ("show_complete", "The complete {subject}", "Here is a complete {subject}.", "hero", "auto"),
        ("reveal_shell", "Fade the outer shell", "Watch the outer shell fade so we can see inside.", "hero", "on_focus"),
        ("explode", "Separate the parts", "Now the {subject} separates into its parts so each one stands on its own.", "hero", "on_tap"),
        ("highlight_key", "The parts that matter", "Each part has a job — let's look at the ones that matter most.", "hero", "on_focus"),
        ("reassemble", "Back together", "And back together — that's how a {subject} fits as a whole.", "hero", "auto"),
    ],
    "cutaway_view": [
        ("show_complete", "The whole {subject}", "Here is the {subject} as you'd normally see it.", "hero", "auto"),
        ("cut", "Slice it open", "We cut the shell away to look straight inside.", "hero", "on_focus"),
        ("trace_internals", "Inside the {subject}", "Follow the interior — this is what the outside hides.", "hero", "on_focus"),
        ("close", "Seal it back", "Seal it back up, now that you know what's within.", "hero", "auto"),
    ],
    "animated_simulation": [
        ("meet", "Meet the {subject}", "Meet the {subject}.", "hero", "auto"),
        ("setup", "What's about to happen", "Here's what's about to happen.", "hero", "on_focus"),
        ("play", "{subject} in motion", "Watch the {subject} in motion.", "hero", "on_tap"),
        ("focus_phase", "The key moment", "Notice this key moment in the cycle.", "hero", "on_focus"),
        ("recap", "The cycle repeats", "That's the full cycle — and it repeats.", "hero", "auto"),
    ],
    "assembly_sequence": [
        ("parts", "Lay out the parts", "These are the parts of the {subject}, laid out in front of you.", "hero", "auto"),
        ("base", "Set the base", "Start with the base — everything builds up from here.", "hero", "on_tap"),
        ("build", "Add each piece", "Add each piece in order so you feel how it goes together.", "hero", "on_tap"),
        ("fasten", "Lock it together", "Fasten the joints so the {subject} holds.", "hero", "on_focus"),
        ("done", "Assembled", "Assembled — that's the finished {subject}.", "hero", "auto"),
    ],
    "timeline": [
        ("start", "Where it begins", "Our story of the {subject} begins here.", "hero", "auto"),
        ("middle", "What changes", "Then things change along the way.", "hero", "on_tap"),
        ("turning_point", "The turning point", "This is the moment that mattered most.", "hero", "on_focus"),
        ("end", "Where it leads", "And this is where it all leads.", "hero", "auto"),
    ],
    "comparison_layout": [
        ("lineup", "The {subject} side by side", "Here are the {subject} placed side by side.", "hero", "auto"),
        ("dimension_1", "First difference", "Compare them on the first thing that matters.", "hero", "on_focus"),
        ("dimension_2", "Second difference", "Now another difference between them.", "hero", "on_focus"),
        ("verdict", "Which fits you", "Here's the trade-off — which one fits you?", "hero", "on_tap"),
    ],
    "real_scale_placement": [
        ("place", "The {subject}, to scale", "Here is the {subject}, at a scale you can walk around.", "hero", "auto"),
        ("take_in", "Take it in", "Take it in — notice the real proportions.", "hero", "on_focus"),
        ("detail", "Look closer", "Look closer at one part of the {subject}.", "hero", "on_tap"),
        ("context", "In real space", "This is how the {subject} really sits in space.", "hero", "auto"),
    ],
    "layered_breakdown": [
        ("whole", "The {subject}", "Start with the {subject} as one whole.", "hero", "auto"),
        ("peel_1", "Peel a layer", "Peel back the first layer to see what's underneath.", "hero", "on_tap"),
        ("peel_2", "Go deeper", "Go one layer deeper.", "hero", "on_tap"),
        ("core", "The core", "And here's the core that everything wraps around.", "hero", "on_focus"),
    ],
    "flow_visualization": [
        ("system", "The system", "Here is the {subject} as a system.", "hero", "auto"),
        ("inject", "Follow the flow", "Watch what flows through it.", "hero", "on_tap"),
        ("bottleneck", "Where it matters", "Notice where the flow concentrates.", "hero", "on_focus"),
        ("recap", "The whole flow", "That's the whole flow, start to finish.", "hero", "auto"),
    ],
    "cause_and_effect": [
        ("setup", "The setup", "Here's the {subject} at rest.", "hero", "auto"),
        ("trigger", "The trigger", "Now we trigger it.", "hero", "on_tap"),
        ("effect", "The effect", "And here's the effect that follows.", "hero", "on_focus"),
        ("link", "The link", "That link between cause and effect is the whole point.", "hero", "auto"),
    ],
    "interactive_sandbox": [
        ("intro", "Your {subject}", "This {subject} is yours to play with.", "hero", "auto"),
        ("controls", "Try the controls", "Try the controls and see what changes.", "hero", "on_tap"),
        ("observe", "Watch what happens", "Watch how it responds to you.", "hero", "on_focus"),
        ("wrap", "What you found", "That's what you can discover here.", "hero", "auto"),
    ],
    "guided_narrative": [
        ("open", "The {subject}", "Let's walk through the {subject} together.", "hero", "auto"),
        ("point_1", "First, this", "First, look here.", "hero", "on_focus"),
        ("point_2", "Then, this", "Then, notice this.", "hero", "on_focus"),
        ("close", "Putting it together", "Put it together and the {subject} makes sense.", "hero", "auto"),
    ],
}

# ── Shared text helpers (used by every classifier so scoring is consistent) ──
_WORD_RE = re.compile(r"[^a-z0-9]+")


def normalize(s: str) -> str:
    """Lowercase, collapse non-alphanumerics to spaces, pad with spaces so
    whole-word and phrase matches are simple `in` / `\\b` tests."""
    return " " + _WORD_RE.sub(" ", (s or "").lower()).strip() + " "


def score_terms(text: str, terms) -> int:
    """Sum signal weights present in already-normalized `text`. Multi-word terms
    score 2 (more specific); single words score 1 (whole-word match)."""
    total = 0
    for t in terms:
        if " " in t:
            if t in text:
                total += 2
        elif re.search(r"\b" + re.escape(t) + r"\b", text):
            total += 1
    return total


def confidence(scores: dict, key: str) -> float:
    """Margin-based confidence in [0.2, 0.95] from a {label: score} dict."""
    if not scores or scores.get(key, 0) <= 0:
        return 0.0
    top = scores[key]
    others = [v for k, v in scores.items() if k != key]
    second = max(others) if others else 0
    return round(min(0.95, max(0.2, (top - second) / top + 0.2)), 3)


# ── Import-time self-check: placement defaults must stay inside es vocab ──────
try:  # pragma: no cover - guarded so the brain still imports if es is absent
    import experience_spec as _es

    for _d, _pl in PLACEMENT_BY_DOMAIN.items():
        assert _pl["anchor"] in _es.ANCHORS, _d
        assert _pl["layout"] in _es.LAYOUTS, _d
        assert _pl["facing"] in _es.FACINGS, _d
        assert _pl["scale_mode"] in _es.SCALE_MODES, _d
    for _a in CLAUSE_TO_ANCHOR.values():
        assert _a in _es.ANCHORS, _a
except ImportError:
    pass
