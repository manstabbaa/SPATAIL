"""scene_contract.py — the SPATAIL Scene Contract v0.6 ("a playable spatial scene").

Evolves the v0.5 modular experience (studio/director/experience.py) from a guided
3D slideshow into a four-section SCENE the MR runtime plays, one section per design
pillar:

    content    (Pillar 1, XR Content Design)      assets + their asset-package manifest
    placement  (Pillar 2, Placement Design System) spatial INTENT/constraints, not coords
    brand      (Pillar 3, SPATAIL Brand)           style tokens — SUPPLIED BY YOU later
    logic      (Runtime / Game Manager)            trigger graph + objectives;
                                                    `beats` become an optional guided track

This is ADDITIVE: it is a pure projection over the existing v0.5 dict, so nothing
that consumes v0.5 breaks. The placement section deliberately carries REQUIREMENTS
(anchor preference, scale mode, relationships) — never hard coordinates — because
the on-device Placement solver resolves them against the live RoomModel at runtime.
"""
from __future__ import annotations

SCHEMA = "0.6.0-spatail-scene"


def _placement_for(modular: dict) -> dict:
    """Spatial requirements the on-device solver resolves against the RoomModel."""
    stage = modular.get("stage", {}) or {}
    assets = modular.get("assets", []) or []
    design = modular.get("placement", {}) or {}     # Placement Design System §12 contract
    primary = next((a["id"] for a in assets if a.get("role") == "primary_object"),
                   assets[0]["id"] if assets else None)
    anchor_pref = (design.get("anchor") or {}).get("type") or stage.get("anchor", "table")
    return {
        "anchorPreference": anchor_pref,                    # table|floor|wall|object|room|free
        "layout": stage.get("layout", "arc"),
        "facing": stage.get("facing", "user"),
        "scaleMode": (design.get("scale") or {}).get("mode") or stage.get("scaleMode", "dynamic"),
        # usable fraction of the anchor surface — the same knob every solver
        # reads as intent.coverage (default 0.8, clamped there) and clients
        # echo back in placement-report solverInputs (Live Brain spec §2.2).
        # Sourced from the design-system preset's scale.maxSurfaceCoverage.
        "coverage": (design.get("scale") or {}).get("maxSurfaceCoverage", 0.8),
        "primary": primary,
        # per-asset footprint requirement (true metres) the solver packs into space
        "footprints": {a["id"]: a.get("scaleMeters", [0.2, 0.2, 0.2]) for a in assets},
        # provenance of those metres (Live Brain spec §2.3): library measured dims |
        # object_size_llm estimate | default_guess — additive beside `footprints`
        # so older runtimes keep reading the plain metres.
        "footprintSources": {a["id"]: a.get("footprintSource", "default_guess")
                             for a in assets},
        # relationships the solver should preserve (filled by composer later)
        "relationships": [],
        # zones the design system can target (level-design vocabulary)
        "zones": ["hero", "secondary", "peripheral"],
        # the full Placement Design System policy (anchor+fallback, scale mode+notes,
        # position/orientation rules, semantic interactions, UI rules, comfort, §10
        # fallbacks) — docs/spatail-placement-design-system.md. The device solver
        # treats THIS as the requirement set; the keys above remain for older runtimes.
        "designSystem": design,
    }


def _content_for(modular: dict) -> list:
    """Assets + a reference to their asset-package manifest (parts/sockets/clips)."""
    out = []
    for a in modular.get("assets", []) or []:
        out.append({
            "id": a["id"],
            "name": a.get("name", a["id"]),
            "role": a.get("role", "part"),
            "usdzUrl": a.get("usdzUrl", ""),
            "glbUrl": a.get("glbUrl", ""),
            "manifestUrl": a.get("manifestUrl", ""),   # asset-package manifest (Phase 1)
            "scaleMeters": a.get("scaleMeters", [0.2, 0.2, 0.2]),
            # where the metres came from (Live Brain spec §2.3)
            "footprintSource": a.get("footprintSource", "default_guess"),
            "capabilities": {
                "animation": bool(a.get("supportsAnimation")),
                "namedParts": bool(a.get("supportsHighlight")),
                "transparency": bool(a.get("supportsTransparency")),
                "physics": bool(a.get("physics") or a.get("supportsPhysics")),
            },
            "status": a.get("status", "placeholder"),
        })
    return out


def _logic_for(modular: dict) -> dict:
    """Game-manager program: the clip SEQUENCE + interaction triggers + objectives,
    produced by the composer (sequencer). SPATAIL plays/sequences the clips."""
    u = modular.get("understanding", {}) or {}
    return {
        "sequence": modular.get("sequence", []),
        "triggers": modular.get("triggers", []),
        "objectives": modular.get("objectives",
                                  [{"id": "understand", "goal": u.get("subject", ""),
                                    "summary": u.get("summary", "")}]),
        "worldState": {"stepIndex": 0},
    }


def to_scene_contract(modular: dict, *, brand: dict | None = None) -> dict:
    """Project the modular experience into a v0.6 Scene Contract. Pure + additive."""
    out = {
        "schemaVersion": SCHEMA,
        "experienceId": modular.get("experienceId"),
        "title": modular.get("title", "Experience"),
        "source": modular.get("source", {}),
        "understanding": modular.get("understanding", {}),
        "content": _content_for(modular),
        "placement": _placement_for(modular),
        # the stream split: anchoring.mode = "object" (ARObjectAnchor on a
        # .referenceobject, tracked overlay) | "world" (solver-resolved world anchor).
        "anchoring": modular.get("anchoring", {"mode": "world", "fallback": "world"}),
        # Pillar 3 is owned by the SPATAIL brand system; until it's supplied the
        # runtime falls back to platform defaults for all spatial UI.
        "brand": brand or {"status": "pending",
                           "note": "SPATAIL brand system supplied separately"},
        "logic": _logic_for(modular),
        "progressive": modular.get("progressive", {}),
        # carry the Blender-build hook through unchanged
        "generation": modular.get("generation"),
        "generationJobId": modular.get("generationJobId"),
    }
    # THE SETTING LAW (staged context, studio/director/settings.py): the
    # brain-composed setting rides the projection unchanged so every runtime
    # stages the same world. Absent in AR / real-room context — additive.
    if modular.get("setting"):
        out["setting"] = modular["setting"]
    return out


if __name__ == "__main__":
    import json
    sample = {
        "experienceId": "exp_demo", "title": "Pendulum",
        "understanding": {"subject": "pendulum", "summary": "A swinging weight."},
        "stage": {"anchor": "table", "layout": "arc", "facing": "user", "scaleMode": "dynamic"},
        "assets": [{"id": "bob", "name": "Bob", "role": "primary_object",
                    "usdzUrl": "/artifacts/bob.usdz", "scaleMeters": [0.1, 0.4, 0.1],
                    "supportsAnimation": True, "status": "processed"}],
        "beats": [{"id": "intro", "title": "Meet the pendulum", "mechanics": []}],
        "capabilities": ["none", "animation"],
    }
    print(json.dumps(to_scene_contract(sample), indent=2))
