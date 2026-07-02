"""asset_manifest.py — the CONTRACT between Blender (asset producer) and SPATAIL
(the game manager / player).

Blender BAKES the asset's motion as named CLIPS — frame-range segments on the
asset's single exported timeline — and lists them here, alongside the named PARTS
(for labels / tap-a-part). SPATAIL does NOT compute motion: it PLAYS and SEQUENCES
these clips, with interaction layered on top.

This replaces the old procedural "mechanics catalog": all movement lives in the
asset as baked animation; the runtime is a clip player + sequencer + trigger graph.

Pure stdlib so it imports anywhere (inside Blender, the server, the composer).
"""
from __future__ import annotations

import re

SCHEMA = "spatail-asset-manifest/4"   # /3 anchors (surface points); /4 regions (story-driven addressable sub-mesh overlays)

# Part name -> semantic role. Used for LABELS and TAP-A-PART targeting only; motion
# is now baked into clips, not derived from the role.
ROLE_KEYWORDS = {
    "crank":    ["crankshaft", "crank"],
    "camshaft": ["camshaft"],
    "piston":   ["piston"],
    "rod":      ["conrod", "connecting", "pushrod", "rod"],
    "valve":    ["valve", "tappet", "lifter"],
    "gear":     ["gear", "cog", "sprocket", "pinion"],
    "wheel":    ["wheel", "tire", "tyre"],
    "fan":      ["fan", "blade", "propeller", "prop", "rotor", "impeller", "turbine"],
    "shaft":    ["driveshaft", "axle", "spindle", "shaft"],
    "belt":     ["belt", "chain", "timing"],
    "body":     ["body", "block", "case", "casing", "housing", "frame", "base",
                 "engine", "mount", "cover", "manifold"],
}


def infer_role(name: str) -> str:
    n = re.sub(r"[._]\d+$", "", (name or "").strip().lower()).replace("-", "_")
    for role, kws in ROLE_KEYWORDS.items():
        if any(k in n for k in kws):
            return role
    return "part"


def enrich_parts(raw_parts: list[dict]) -> list[dict]:
    """Attach a semantic role to Blender's per-part geometry facts (name/pivot/size).
    For labels and tap-a-part; no motion (that lives in clips)."""
    out = []
    for p in raw_parts or []:
        out.append({
            "name": p.get("name"),
            "role": infer_role(p.get("name", "")),
            "pivot_m": p.get("pivot_m", [0.0, 0.0, 0.0]),
            "size_m": p.get("size_m"),
        })
    return out


# ── anchors (schema /3): Blender-computed semantic surface points ────────────────

def normalize_anchors(raw, max_anchors: int = 8) -> list[dict]:
    """Validate the anchors the Blender anchors stage computed. Each anchor is a
    semantic point ON the mesh in the asset's exported Y-up space: pos_m in metres,
    `offset` normalized to the bbox ((pos − min) / extents — exactly the phone's
    step.anchorOffset convention), a unit normal, and a 0..1 view-consensus
    confidence. Names are snake_case so the director can reference them."""
    out, seen = [], set()
    for a in raw or []:
        try:
            name = re.sub(r"[^a-z0-9]+", "_", str(a.get("name") or "").lower()).strip("_")[:48]
            off = [min(max(float(x), 0.0), 1.0) for x in (a.get("offset") or [])][:3]
            if not name or name in seen or len(off) != 3:
                continue
            seen.add(name)
            out.append({
                "name": name,
                "pos_m": [float(x) for x in (a.get("pos_m") or [0, 0, 0])][:3],
                "normal": [float(x) for x in (a.get("normal") or [0, 1, 0])][:3],
                "offset": off,
                "confidence": min(max(float(a.get("confidence", 0.5)), 0.0), 1.0),
                "verified": bool(a.get("verified", False)),
            })
        except Exception:
            continue
        if len(out) >= max_anchors:
            break
    return out


# ── regions (schema /4): story-driven addressable sub-mesh overlays ──────────────

def normalize_regions(raw, max_regions: int = 16) -> list[dict]:
    """Validate the regions the Blender regions stage baked. A region turns a
    STORY-required part (the lion's eye) into something the runtime can light up
    PRECISELY: a separate overlay mesh baked over just that patch of surface, named
    so the runtime can find it, plus a normalized `offset` (same bbox convention as
    anchors / iOS step.anchorOffset) for the callout, and the effect families the
    brief said the part must support.

    Each record:
      {id, label, role, overlayNode, offset:[x,y,z]∈[0,1]³, radiusNorm,
       effects:[…], verified}
    `overlayNode` is the exported entity name (`spatail_region__<mesh>__<id>`) the iOS
    runtime enables + effects; the base mesh is never touched (non-destructive)."""
    out, seen = [], set()
    for r in raw or []:
        try:
            rid = re.sub(r"[^a-z0-9]+", "_", str(r.get("id") or "").lower()).strip("_")[:48]
            node = str(r.get("overlayNode") or "").strip()[:128]
            off = [min(max(float(x), 0.0), 1.0) for x in (r.get("offset") or [])][:3]
            if not rid or rid in seen or not node or len(off) != 3:
                continue
            seen.add(rid)
            effects, eseen = [], set()
            for e in r.get("effects") or ["highlight"]:
                fam = str(e or "").strip().lower()
                fam = fam.split(":", 1)[0] if fam.startswith("tint:") else fam
                if fam in ("highlight", "emissive", "ghost", "tint", "none") and fam not in eseen:
                    eseen.add(fam)
                    effects.append(fam)
            out.append({
                "id": rid,
                "label": str(r.get("label") or rid.replace("_", " ").title())[:64],
                "role": re.sub(r"[^a-z0-9]+", "_", str(r.get("role") or rid).lower()).strip("_")[:48],
                "overlayNode": node,
                "offset": [round(o, 4) for o in off],
                "radiusNorm": round(min(max(float(r.get("radiusNorm", 0.1)), 0.0), 1.0), 4),
                "effects": effects or ["highlight"],
                "verified": bool(r.get("verified", False)),
            })
        except Exception:
            continue
        if len(out) >= max_regions:
            break
    return out


# ── clips: the new vocabulary (named baked segments of the asset's timeline) ─────

def default_clips(frame_end: int, fps: int = 30) -> list[dict]:
    """When the author didn't declare segments, the whole baked timeline is one
    looping clip named 'demo'."""
    return [{"name": "demo", "role": "demo", "start": 1, "end": int(max(frame_end, 1)),
             "fps": int(fps), "loop": True}]


def normalize_clips(raw, frame_end: int, fps: int = 30) -> list[dict]:
    """Validate the clips the author baked (reported via result['clips']); fall back
    to a single 'demo' clip spanning the whole timeline."""
    out = []
    for c in raw or []:
        try:
            name = str(c.get("name") or "clip").strip() or "clip"
            s = int(c.get("start", 1))
            e = int(c.get("end", frame_end))
            if e <= s:
                continue
            out.append({"name": name, "role": str(c.get("role") or name),
                        "start": s, "end": e, "fps": int(c.get("fps", fps)),
                        "loop": bool(c.get("loop", True))})
        except Exception:
            continue
    return out or default_clips(frame_end, fps)


def primary_clip(clips: list[dict]) -> str:
    """The clip the runtime plays by default (idle/demo/running)."""
    if not clips:
        return "demo"
    for pref in ("running", "demo", "idle"):
        for c in clips:
            if c.get("name") == pref:
                return pref
    return clips[0]["name"]


if __name__ == "__main__":
    import json
    print(json.dumps(normalize_clips(
        [{"name": "running", "start": 1, "end": 120},
         {"name": "explode", "start": 121, "end": 180, "loop": False}], 180), indent=2))
    print("parts:", json.dumps(enrich_parts(
        [{"name": "piston_1"}, {"name": "engine_block"}]), indent=2))
