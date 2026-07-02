"""region_schema.py — platform-neutral region/hotspot emphasis schema.

The Master File's INTERACTION pillar says an object's parts get emphasized by
INTENT — "look here", "this is active", "see inside", "this is hot" — not by a
platform's material API. The legacy iOS path encoded this as a per-step `effect`
string (highlight / emissive / ghost / tint) that mapped 1:1 to RealityKit
PhysicallyBasedMaterial mutations (SpatailMaterials.swift). For the Android XR
pivot (2026) the contract instead carries a neutral HOTSPOT that each runtime
interprets in its own renderer (Jetpack SceneCore material/alpha/baseColor on
Android, RealityKit on iOS, three.js on the web).

Hotspot shape (additive to the step; the legacy `effect` string is kept for the
frozen iOS reference path):

    {"region": "<baked region / anchor id>",
     "emphasis": {"kind": "emphasize|reveal|recolor|marker",
                  "style": "glow|energize|translucent",   # emphasize / reveal
                  "color": "#rrggbb",                      # recolor only
                  "intensity": 0.0-1.0}}

kind semantics (renderer-agnostic):
  emphasize  — draw the eye to this region without changing what it is
               (style "glow" = subtle accent / "look here";
                style "energize" = strong self-illumination / "active, powered")
  reveal     — let the user see structure (style "translucent" = make it see-through)
  recolor    — set a state colour (`color`), e.g. hot=red, cold=blue
  marker     — callout / label only; no change to the region's look
"""
from __future__ import annotations

EMPHASIS_KINDS = ("emphasize", "reveal", "recolor", "marker")


def effect_to_emphasis(effect: str | None) -> dict:
    """Translate a legacy iOS `effect` string into a neutral emphasis dict."""
    e = (effect or "").strip().lower()
    if not e or e == "none":
        return {"kind": "marker", "intensity": 1.0}
    if e == "highlight":
        return {"kind": "emphasize", "style": "glow", "intensity": 1.0}
    if e == "emissive":
        return {"kind": "emphasize", "style": "energize", "intensity": 1.0}
    if e == "ghost":
        return {"kind": "reveal", "style": "translucent", "intensity": 1.0}
    if e.startswith("tint:"):
        return {"kind": "recolor", "color": f"#{e[5:].lstrip('#')}", "intensity": 1.0}
    # unknown vocabulary → safe default emphasis
    return {"kind": "emphasize", "style": "glow", "intensity": 1.0}


def to_hotspot(region: str | None, effect: str | None = None) -> dict | None:
    """Neutral hotspot for a step, or None when there is no region to emphasize."""
    if not region:
        return None
    return {"region": region, "emphasis": effect_to_emphasis(effect)}
