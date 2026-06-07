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
    primary = next((a["id"] for a in assets if a.get("role") == "primary_object"),
                   assets[0]["id"] if assets else None)
    return {
        "anchorPreference": stage.get("anchor", "table"),   # table|floor|wall|free
        "layout": stage.get("layout", "arc"),
        "facing": stage.get("facing", "user"),
        "scaleMode": stage.get("scaleMode", "dynamic"),     # dynamic|tabletop|real
        "primary": primary,
        # per-asset footprint requirement (true metres) the solver packs into space
        "footprints": {a["id"]: a.get("scaleMeters", [0.2, 0.2, 0.2]) for a in assets},
        # relationships the solver should preserve (filled by composer later)
        "relationships": [],
        # zones the design system can target (level-design vocabulary)
        "zones": ["hero", "secondary", "peripheral"],
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
    """Game-manager program: guided track + trigger graph + objectives. Derived by
    director/logic.py from the composed beats/mechanics (the interactive layer)."""
    u = modular.get("understanding", {}) or {}
    beats = modular.get("beats", []) or []
    assets = modular.get("assets", []) or []
    try:
        import sys
        from pathlib import Path
        _d = str(Path(__file__).resolve().parent)
        if _d not in sys.path:
            sys.path.insert(0, _d)
        import logic
        return logic.build_logic(u, beats, assets)
    except Exception:  # noqa: BLE001 — never fail contract assembly over the logic layer
        return {"guidedTrack": beats, "triggers": [],
                "objectives": [{"id": "understand", "goal": u.get("subject", ""),
                                "summary": u.get("summary", "")}],
                "worldState": {}}


def to_scene_contract(modular: dict, *, brand: dict | None = None) -> dict:
    """Project a v0.5 modular experience into a v0.6 Scene Contract. Pure + additive."""
    return {
        "schemaVersion": SCHEMA,
        "experienceId": modular.get("experienceId"),
        "title": modular.get("title", "Experience"),
        "source": modular.get("source", {}),
        "understanding": modular.get("understanding", {}),
        "content": _content_for(modular),
        "placement": _placement_for(modular),
        # Pillar 3 is owned by the SPATAIL brand system; until it's supplied the
        # runtime falls back to platform defaults for all spatial UI.
        "brand": brand or {"status": "pending",
                           "note": "SPATAIL brand system supplied separately"},
        "logic": _logic_for(modular),
        "mechanicsManifest": modular.get("mechanicsManifest", "/mechanics/mechanics.json"),
        "capabilities": modular.get("capabilities", []),
        "progressive": modular.get("progressive", {}),
        # carry the Blender-build hook through unchanged
        "generation": modular.get("generation"),
        "generationJobId": modular.get("generationJobId"),
    }


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
