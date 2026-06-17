"""asset_brief.py — the STORY→ASSET requirements contract (slice 1: addressable parts).

The director plans the experience FIRST. Before the asset artist (Meshy + Blender)
runs, the director emits an AssetBrief: the per-asset requirements the STORY imposes
on the model — which named parts the lesson will point at / make emissive, and how.

Today the order is reversed: Meshy builds the mesh blind, the generic anchors pass
guesses "3-6 meaningful features", and the lion's EYE — the exact part a story wants
to highlight — gets dropped (studio/out/meshy/_lion_anchors.json: dropped ["eye", …]).
The brief flips that: the story DECLARES "eye must be addressable + emissive", and the
producer is steered to find it, bake it as a precise region, and ship it.

This module is pure stdlib (no bpy, no network) so it imports in the director, the
server, and inside Blender. It is the single source of truth for the brief shape that
asset_service (producer) and the iOS runtime (consumer) both obey.

Brief shape (spatail-asset-brief/1):
    {
      "schema": "spatail-asset-brief/1",
      "assetId": "lion",
      "parts": [
        {"id": "eye", "role": "eye", "aliases": ["eye", "left eye"],
         "addressable": true, "effects": ["emissive", "highlight"]}
      ]
    }

`addressable` parts are the slice-1 deliverable: each must become a stable, separately
effect-able REGION of the asset (a baked overlay mesh + a manifest region record), so a
step's effect lands on exactly that part instead of the whole mesh.
"""
from __future__ import annotations

import re

SCHEMA = "spatail-asset-brief/1"

# Effect families the runtime can express on-device (composer._clean_effect mirror).
# "tint:#hex" collapses to the family "tint" for the brief's capability list.
_EFFECT_FAMILIES = {"highlight", "emissive", "ghost", "tint", "none"}
_DEFAULT_EFFECTS = ["highlight"]


def _slug(s: str) -> str:
    """snake_case id, matching anchors/region naming (lowercase a-z0-9_)."""
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:48]


def _effect_family(effect: str) -> str:
    """Map a step.effect ("tint:#ff0000", "emissive", "") to its family."""
    v = str(effect or "").strip().lower()
    if not v:
        return "highlight"
    if v.startswith("tint:"):
        return "tint"
    return v if v in _EFFECT_FAMILIES else "highlight"


def required_parts_from_sequence(sequence: list[dict], asset_id: str) -> list[dict]:
    """Derive the addressable-part requirements from a composed step sequence.

    Each step that focuses `asset_id`, names a `target` part, and asks for a visible
    effect (anything but "none") becomes a required addressable part. The story is
    therefore the source of the requirement: "step 2 makes the eye emissive" ⇒ the eye
    must be a real region.
    """
    by_id: dict[str, dict] = {}
    for s in sequence or []:
        focus = s.get("focus")
        if asset_id and focus not in (asset_id, "scene"):
            continue
        target = _slug(s.get("target") or s.get("anchor") or "")
        if not target:
            continue
        fam = _effect_family(s.get("effect"))
        if fam == "none":
            continue
        part = by_id.setdefault(
            target, {"id": target, "role": target, "aliases": [], "addressable": True,
                     "effects": []})
        if fam not in part["effects"]:
            part["effects"].append(fam)
    out = []
    for p in by_id.values():
        p["effects"] = p["effects"] or list(_DEFAULT_EFFECTS)
        out.append(p)
    return out


def build_brief(asset_id: str, *, sequence: list[dict] | None = None,
                parts: list[dict] | None = None) -> dict:
    """Assemble + validate a brief for one asset. `parts` (explicit) take priority;
    otherwise the required parts are derived from `sequence`."""
    raw = list(parts) if parts else required_parts_from_sequence(sequence or [], asset_id)
    return validate({"schema": SCHEMA, "assetId": _slug(asset_id), "parts": raw})


def validate(brief: dict) -> dict:
    """Normalize a brief to the canonical shape (clean ids/aliases/effects, drop dupes)."""
    asset_id = _slug((brief or {}).get("assetId") or "")
    seen: set[str] = set()
    parts: list[dict] = []
    for p in (brief or {}).get("parts") or []:
        pid = _slug(p.get("id") or p.get("role") or "")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        aliases, aseen = [], {pid}
        for a in [p.get("role")] + list(p.get("aliases") or []):
            al = _slug(a or "")
            if al and al not in aseen:
                aseen.add(al)
                aliases.append(al)
        effects, eseen = [], set()
        for e in p.get("effects") or _DEFAULT_EFFECTS:
            fam = _effect_family(e)
            if fam not in eseen:
                eseen.add(fam)
                effects.append(fam)
        parts.append({"id": pid, "role": _slug(p.get("role") or pid),
                      "aliases": aliases, "addressable": bool(p.get("addressable", True)),
                      "effects": effects})
    return {"schema": SCHEMA, "assetId": asset_id, "parts": parts}


def addressable_parts(brief: dict) -> list[dict]:
    """The parts that must become regions (the slice-1 producer worklist)."""
    return [p for p in (brief or {}).get("parts") or [] if p.get("addressable", True)]


def required_part_names(brief: dict) -> list[str]:
    """All ids + aliases the producer should try to localize — biases the anchors
    stage's Gemini prompt so the story's parts are FOUND instead of dropped."""
    out, seen = [], set()
    for p in (brief or {}).get("parts") or []:
        for name in [p.get("id")] + list(p.get("aliases") or []):
            n = _slug(name or "")
            if n and n not in seen:
                seen.add(n)
                out.append(n)
    return out


def has_requirements(brief: dict | None) -> bool:
    return bool(brief and addressable_parts(brief))


if __name__ == "__main__":
    import json
    seq = [
        {"id": "intro", "focus": "lion", "title": "Lion", "target": ""},
        {"id": "eye", "focus": "lion", "title": "The eye", "target": "eye", "effect": "emissive"},
        {"id": "mane", "focus": "lion", "title": "The mane", "target": "mane", "effect": "tint:#ffaa00"},
        {"id": "off", "focus": "lion", "target": "tail", "effect": "none"},
    ]
    print(json.dumps(build_brief("lion", sequence=seq), indent=2))
    print("required names:", required_part_names(build_brief("lion", sequence=seq)))
