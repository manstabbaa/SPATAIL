"""director_fixtures.py - deterministic question->scene routing for known topics.

This lets SPATAIL EDUCATOR answer common questions WITHOUT an LLM in the loop, so
the pipeline is demoable offline and in tests. It is NOT a content fallback: it
only returns scene specs whose beats map to REAL, fully-built Blender demos. For
anything it doesn't recognise it returns None, and educator.py asks the Director
agent to author a fresh spec (which may require the Artist to build new
real-world objects). Either way, no primitive ever stands in for a real object.

Each fixture mirrors the schema in studio/scenes/newtons_laws.json.
"""
from __future__ import annotations

import json
from pathlib import Path

SCENES = Path(__file__).resolve().parent / "scenes"

# topic -> the canonical scene spec file that already exists and is fully real
_TOPIC_TO_SCENE = {
    "newtons_laws": SCENES / "newtons_laws.json",
}

# keyword sets that route a free-text question to a topic
_ROUTES = [
    ({"newton", "inertia", "f = ma", "f=ma", "action", "reaction",
      "laws of motion", "law of motion"}, "newtons_laws"),
]


def route(brief: dict) -> dict | None:
    """Return a scene-spec dict for the brief, or None if no fixture matches."""
    q = (brief.get("prompt") or "").lower()
    topic = None
    for keywords, t in _ROUTES:
        if any(k in q for k in keywords):
            topic = t
            break
    if not topic:
        return None
    spec_path = _TOPIC_TO_SCENE.get(topic)
    if not spec_path or not spec_path.exists():
        return None
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    # retarget the spec's sceneId to this brief's scene id so the build writes to
    # the brief's own slot (keeps multiple questions from colliding)
    spec["sceneId"] = brief.get("scene", spec.get("sceneId"))
    return spec


def known_topics() -> list[str]:
    return sorted(_TOPIC_TO_SCENE)
