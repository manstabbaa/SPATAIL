"""composer.py — the SPATAIL sequencer (replaces the old mechanics catalog).

Motion is BAKED into each asset as named clips (the Blender->SPATAIL contract,
asset_manifest.py). This module no longer composes procedural mechanics; it DESIGNS
the experience as:
  - sequence : ordered steps, each PLAYS a clip + shows narration/panels
  - triggers : interaction layered on top (onTap/onApproach/onGaze -> play/advance/label)
SPATAIL (the runtime game-manager) plays and sequences these clips.

Two paths, same output shape: llm.compose (Gemini) for freedom, deterministic for
the always-on offline guarantee.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # studio/ for asset_manifest
import asset_manifest as am  # noqa: E402


def _clip_names(asset: dict) -> list[str]:
    return [c.get("name") for c in (asset.get("clips") or []) if c.get("name")]


def _anchor_for(step: dict, asset: dict | None) -> dict | None:
    """Map a step to one of the focus asset's Blender-computed surface anchors so the
    callout lands ON the mesh ("the frog's eye") instead of a guessed bbox corner.
    Prefers an explicit `anchor`/`target` name the LLM gave, else picks the anchor
    whose name words best overlap the step's title + narration."""
    anchors = (asset or {}).get("anchors") or []
    if not anchors:
        return None
    by_name = {a["name"]: a for a in anchors if a.get("name")}
    want = re.sub(r"[^a-z0-9]+", "_",
                  str(step.get("anchor") or step.get("target") or "").lower()).strip("_")
    if want in by_name:
        return by_name[want]
    text = f"{step.get('title', '')} {step.get('narration', '')}".lower()
    best, best_score = None, 0
    for a in anchors:
        words = [w for w in a["name"].split("_") if len(w) > 2] or [a["name"]]
        score = sum(1 for w in words if w in text)
        if score > best_score:
            best, best_score = a, score
    return best if best_score > 0 else None


def _hero_clips(assets: list[dict]) -> list[dict]:
    return (assets[0].get("clips") if assets else None) or am.default_clips(120)


def deterministic_sequence(understanding: dict, assets: list[dict]) -> list[dict]:
    """A solid, varied sequence from rules — the offline guarantee."""
    subject = (understanding.get("subject") or "this").strip()
    summary = understanding.get("summary", "") or f"Let's look at {subject}."
    if not assets:
        return [{"id": "intro", "title": subject.title(), "narration": summary,
                 "focus": "scene", "clip": "demo", "loop": True,
                 "panels": [{"kind": "title", "title": subject.title(), "body": summary}],
                 "advance": "tap"}]
    hero = assets[0]
    clips = _hero_clips(assets)
    primary = am.primary_clip(clips)
    steps = [{
        "id": "intro", "title": subject.title(), "narration": summary,
        "focus": hero["id"], "clip": primary, "loop": True,
        "panels": [{"kind": "title", "title": subject.title(), "body": summary}],
        "advance": "tap",
    }]
    # a step per ADDITIONAL baked clip (play that segment)
    for c in clips:
        if c["name"] == primary:
            continue
        steps.append({
            "id": f"clip_{c['name']}", "title": c["name"].replace("_", " ").title(),
            "narration": f"{subject.title()} — {c['name'].replace('_', ' ')}.",
            "focus": hero["id"], "clip": c["name"], "loop": bool(c.get("loop", True)),
            "panels": [], "advance": "tap",
        })
    # a step per additional asset
    for a in assets[1:]:
        a_clip = (_clip_names(a) or [primary])[0]
        steps.append({
            "id": f"asset_{a['id']}", "title": a.get("name", a["id"]),
            "narration": a.get("description") or f"Here's {a.get('name', a['id'])}.",
            "focus": a["id"], "clip": a_clip, "loop": True, "panels": [], "advance": "tap",
        })
    steps.append({
        "id": "recap", "title": "Recap", "narration": f"That's {subject}.",
        "focus": hero["id"], "clip": primary, "loop": True,
        "panels": [{"kind": "quiz", "title": "Check", "question": f"What did we explore?",
                    "options": [subject.title(), "Something else"], "answer": 0}],
        "advance": "tap",
    })
    return steps


def _triggers(understanding: dict, assets: list[dict]) -> list[dict]:
    if not assets:
        return []
    hero = assets[0]["id"]
    out = [
        {"when": {"event": "onTap", "target": "scene"}, "do": [{"action": "advance"}]},
        {"when": {"event": "onApproach", "target": hero, "params": {"radius_m": 0.7}},
         "do": [{"action": "playClip"}]},
        {"when": {"event": "onGaze", "target": hero, "params": {"dwell_s": 1.0}},
         "do": [{"action": "label", "target": hero}]},
    ]
    for a in assets:
        for p in (a.get("parts") or [])[:12]:
            nm = p.get("name")
            if nm:
                out.append({"when": {"event": "onTap", "target": nm},
                            "do": [{"action": "label", "target": nm}]})
    return out


# Per-step visual EFFECT vocabulary (Phase 2). The brain names how the step's target
# part should READ; the runtime renders it non-destructively (SpatailMaterials.swift):
#   highlight  — emissive accent glow, keeps the part's look (default; "look here")
#   emissive   — strong self-illumination ("this part is active / energized / powered")
#   ghost      — make the target part translucent ("see through / inside this")
#   tint:#hex  — recolour to a state colour ("hot" red, "cold" blue), keeps the finish
#   none       — no material change (callout/marker only)
# Empty/unknown → runtime defaults to highlight. USDZ→UsdPreviewSurface only carries
# baseColor/emissive/opacity, so every effect here is expressible on-device.
_EFFECTS = {"highlight", "emissive", "ghost", "none"}
_HEX6_RE = re.compile(r"[0-9a-f]{6}")


def _clean_effect(val) -> str:
    """Validate a step's effect against the vocabulary; return '' for unknown so the
    runtime falls back to its default (highlight)."""
    if not val:
        return ""
    v = str(val).strip().lower()
    if v in _EFFECTS:
        return v
    if v.startswith("tint:"):
        hexpart = v[5:].lstrip("#")
        if _HEX6_RE.fullmatch(hexpart):
            return f"tint:#{hexpart}"
    return ""


def _validate_sequence(seq: list[dict], assets: list[dict]) -> list[dict]:
    """Keep steps sane: real focus asset, a clip name that exists on the focus asset
    (else its primary), bounded panels. ALSO pins each step to a surface anchor when
    the focus asset carries Blender-computed anchors → emits step.target +
    step.anchorOffset (the phone decodes both; anchorOffset is the on-mesh point), and
    carries a validated per-step `effect` (the brain's visual-emphasis choice)."""
    ids = {a["id"] for a in assets} | {"scene"}
    hero = assets[0]["id"] if assets else "scene"
    by_id = {a["id"]: a for a in assets}
    clean = []
    for i, s in enumerate(seq or []):
        focus = s.get("focus") if s.get("focus") in ids else hero
        asset = by_id.get(focus)
        names = _clip_names(asset) if asset else []
        clip = s.get("clip")
        if clip not in names:
            clip = am.primary_clip(asset.get("clips")) if asset and asset.get("clips") else "demo"
        step = {
            "id": s.get("id") or f"step_{i}",
            "title": str(s.get("title", "") or "")[:120],
            "narration": str(s.get("narration", "") or "")[:400],
            "focus": focus, "clip": clip, "loop": bool(s.get("loop", True)),
            "panels": (s.get("panels") or [])[:4],
            "advance": s.get("advance", "tap") if s.get("advance") in ("tap", "auto") else "tap",
        }
        eff = _clean_effect(s.get("effect"))
        if eff:
            step["effect"] = eff
        # anchoring: a resolved anchor wins; else keep an explicit anchorOffset the
        # LLM authored (tolerant — the whitelist no longer strips these keys).
        anc = _anchor_for(s, asset)
        if anc:
            step["target"] = anc["name"]
            step["anchorOffset"] = anc["offset"]
        else:
            off = [float(x) for x in (s.get("anchorOffset") or [])][:3]
            if len(off) == 3:
                step["anchorOffset"] = off
            if s.get("target"):
                step["target"] = str(s["target"])[:64]
        clean.append(step)
    return clean


def compose(understanding: dict, assets: list[dict], *, use_llm: bool = True) -> dict:
    """Design the experience as a clip SEQUENCE + triggers. Tries Gemini, falls back
    to the deterministic sequencer."""
    composer = "deterministic"
    sequence = None
    if use_llm:
        try:
            import llm
            raw = llm.compose(understanding, assets, _hero_clips(assets))
            sequence = _validate_sequence(raw, assets)
            if sequence and any(s.get("clip") for s in sequence):
                composer = "gemini"
            else:
                sequence = None
        except Exception as e:  # noqa: BLE001
            print(f"[director] LLM sequencer unavailable, using rules: {e!r}"[:200])
            sequence = None
    if not sequence:
        sequence = _validate_sequence(deterministic_sequence(understanding, assets), assets)
    triggers = _triggers(understanding, assets)
    objectives = [{"id": "understand", "goal": understanding.get("subject", ""),
                   "summary": understanding.get("summary", "")}]
    clips = _hero_clips(assets)
    return {"composer": composer, "sequence": sequence, "triggers": triggers,
            "clips": clips, "objectives": objectives,
            "clipsUsed": sorted({s["clip"] for s in sequence if s.get("clip")})}
