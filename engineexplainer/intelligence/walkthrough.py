"""usermanualXR — manual → XR walkthrough builder.

End-to-end: a manual's text comes in, an XR walkthrough contract goes out.

  build_walkthrough(manual_text) ->
    {
      "ok": bool,
      "asset_id": "fan",
      "asset_glb": "../engine/fan.glb",
      "manual_plan": {...},       # from the ingest agent
      "match": {...},             # which library asset + why
      "contract": {...},          # the playable walkthrough contract
    }

Pipeline:
  1. ingest_manual(text)          — LLM agent: classify product + extract steps
  2. match_product(kind, kw)      — pick the library model
  3. load that asset's registry + animation library
  4. stage each step → one contract beat (deterministic mapping by action)
  5. maybe_bake_from_contract     — bake any motion the model lacks
  6. return the contract + chosen asset

The step→beat staging is deterministic (one beat per manual step). The
"smart" part is the ingest agent understanding ANY manual; the staging is
a mechanical mapping that honors the asset's director_hints.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

# Dual-style imports: works whether this module is imported as part of the
# `intelligence` package (relative) or as a top-level module when server.py
# is run as a script with its own dir on sys.path (absolute).
try:
    from .manual_ingest import ingest_manual
    from . import asset_library as lib
except ImportError:
    from manual_ingest import ingest_manual
    import asset_library as lib

try:
    try:
        from .bake_bridge import maybe_bake_from_contract
    except ImportError:
        from bake_bridge import maybe_bake_from_contract
except Exception:  # bake bridge optional (needs Blender); degrade gracefully
    maybe_bake_from_contract = None


def _segment_fns():
    """Lazy import of the generative path (segment agent + build bridge).
    Kept lazy so the match-only path doesn't require Blender / anthropic."""
    try:
        from .manual_segment import segment_manual
        from .generative_bridge import build_and_register
    except ImportError:
        from manual_segment import segment_manual
        from generative_bridge import build_and_register
    return segment_manual, build_and_register


# ---------------------------------------------------------------------------
# Part resolution
# ---------------------------------------------------------------------------

_MESH_RE = re.compile(r"^mesh\d+$")


def _real_mesh_set(registry) -> set[str]:
    """Every mesh id the GLB actually contains: curated parts + all
    kinematic-group members. Aliases that point at Blender-only nodes
    (e.g. the `fan_hub` Empty) are intentionally NOT in here, so the
    resolver can reject them."""
    s = set(registry.get("parts", {}).keys())
    for g in registry.get("kinematicGroups", []):
        s.update(g.get("members", []))
    return s


def _resolve_one(noun, registry, real_set) -> str | None:
    """Map ONE free-text noun to a single real mesh id, or None.

    Resolution order (most specific first):
      0. raw mesh id  ("mesh48")              -> itself
      1. exact alias  ("hub", "frame_front")  -> its real mesh
      2. part role    ("frame"≈"frame_plate") -> first matching part (exact role wins)
      3. substring alias                       -> its real mesh
      4. kinematic group ("rotor", "blades")  -> first real member
    """
    n = (noun or "").strip().lower()
    if not n:
        return None
    aliases = registry.get("aliases", {})
    parts = registry.get("parts", {})
    groups = registry.get("kinematicGroups", [])

    # 0) raw mesh id straight through
    if _MESH_RE.match(n) and n in real_set:
        return n

    # 1) exact alias -> real mesh
    for alias, real in aliases.items():
        if n == alias.lower() and isinstance(real, str) and real in real_set:
            return real

    # 2) curated part by role (exact role match wins immediately, else first substring hit)
    role_hit = None
    for pid, meta in parts.items():
        if pid not in real_set:
            continue
        role = str(meta.get("role", "")).lower()
        if not role:
            continue
        if n == role:
            return pid
        if role_hit is None and (n in role or role in n):
            role_hit = pid
    if role_hit:
        return role_hit

    # 3) substring alias -> real mesh
    for alias, real in aliases.items():
        al = alias.lower()
        if (n in al or al in n) and isinstance(real, str) and real in real_set:
            return real

    # 4) kinematic group whose id matches -> first real member
    for g in groups:
        gid = g.get("group_id", "").lower()
        if n == gid or n in gid or gid in n or (
           n in ("blade", "blades", "rotor", "impeller", "fan") and gid == "rotor"):
            for m in g.get("members", []):
                if m in real_set:
                    return m

    # 5) last resort: split multi-word nouns and retry each token
    toks = [t for t in re.split(r"[^a-z0-9]+", n) if t and t not in ("the", "a", "an", "seven", "four", "two")]
    if len(toks) > 1:
        for t in toks:
            hit = _resolve_one(t, registry, real_set)
            if hit:
                return hit
    return None


def _resolve_pairs(free_text_parts, registry) -> list[tuple[str, str]]:
    """Resolve each manual noun to a (real_mesh_id, original_noun) pair,
    de-duped by mesh id but preserving manual order. The noun travels with
    its target so labels read the correct text for the part they point at."""
    real = _real_mesh_set(registry)
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for noun in (free_text_parts or []):
        t = _resolve_one(noun, registry, real)
        if t and t not in seen:
            seen.add(t)
            pairs.append((t, (noun or "").strip()))
    return pairs


def _group_for_parts(free_text_parts, registry):
    """Return the kinematicGroup whose members best cover the named parts,
    plus its driven_by_action (for motion staging)."""
    groups = registry.get("kinematicGroups", [])
    # keyword shortcut: rotor/blades/impeller → rotor; piston/crank → those
    text = " ".join(free_text_parts or []).lower()
    for g in groups:
        gid = g["group_id"].lower()
        if gid in text or any(w in text for w in gid.split("_")):
            return g
        if gid == "rotor" and any(k in text for k in ("blade", "rotor", "impeller", "fan")):
            return g
        if gid == "crankshaft" and any(k in text for k in ("crank", "shaft")):
            return g
    return groups[0] if groups else None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "step").lower()).strip("-") or "step"


# ---------------------------------------------------------------------------
# Beat staging — one manual step → one contract beat
# ---------------------------------------------------------------------------

INDIGO = "#5046E5"
HEAT = "#F0883E"
GREEN = "#22A565"


def _stage_step(step, registry, anim_lib, hints, is_first, is_last):
    """Return (beat_dict, list_of_bake_intents) for one manual step."""
    action = (step.get("action") or "none").lower()
    parts = step.get("target_parts", [])
    pairs = _resolve_pairs(parts, registry)
    resolved = [t for t, _ in pairs]
    primary_text = pairs[0][1].title() if pairs else "Part"
    group = _group_for_parts(parts, registry)
    bg_presets = hints.get("preferred_camera_presets", ["hero_threequarter"])
    hero = bg_presets[0] if bg_presets else "hero_threequarter"
    dim_min = hints.get("dim_others_min_factor", 0.45)
    dim_max = hints.get("dim_others_max_factor", 0.6)
    dim_factor = round((dim_min + dim_max) / 2, 2)

    # Narration: the instruction, plus spec/warning if present
    narration = (step.get("instruction") or step.get("title") or "").strip()
    if step.get("spec"):
        narration = f"{narration}  ({step['spec']})".strip()

    words = max(1, len(narration.split()))
    duration = max(4.0, round(words / 12.0 + 1.0, 1))

    actions: list[dict] = []
    bakes: list[dict] = []

    # Beat 0 / identify: whole product, hero camera, label only
    if is_first or action == "identify":
        actions.append({"type": "move_camera", "to": {"preset": hero},
                        "ease": "easeInOut", "duration": 0.9, "startAt": 0})
        for t, noun in pairs[:3]:
            actions.append({"type": "label", "target": t,
                            "text": noun.title() if noun else "Part",
                            "kicker": "PART", "anchor": "auto", "startAt": 0})
        if not resolved:
            actions.append({"type": "label", "target": _any_target(registry),
                            "text": "Overview", "kicker": "STEP", "anchor": "auto", "startAt": 0})
        return _beat(step, narration, duration, actions), bakes

    # Motion steps → play the group's driven action (or bake one)
    if action in ("power_on", "rotate", "slide", "verify"):
        clip = (group or {}).get("driven_by_action")
        have = clip and clip in (anim_lib.get("animations", {}))
        # focus + light dim so the moving part reads
        if resolved:
            actions.append({"type": "dim_others", "except": resolved,
                            "factor": dim_factor, "startAt": 0})
            actions.append({"type": "highlight", "target": resolved[0],
                            "color": HEAT if action == "verify" else INDIGO,
                            "intensity": 1.0, "startAt": 0})
            actions.append({"type": "label", "target": resolved[0],
                            "text": primary_text,
                            "kicker": action.upper().replace("_", " "),
                            "anchor": "auto", "startAt": 0})
        if have:
            actions.append({"type": "play_animation", "animation": clip,
                            "from": 0, "to": 1, "rate": 1.0, "loop": True, "startAt": 0})
        elif group:
            # bake a spin for the group around the asset's rotation axis
            name = f"{group['group_id']}_motion"
            bakes.append({
                "type": "bake_animation", "animation": name,
                "parts": group.get("members", []),
                "motion": "spin",
                "axis": registry.get("rotation_axis_world") or [0, 1, 0],
                "cycles_per_loop": 1, "frames": 120, "startAt": 0,
            })
            actions.append({"type": "bake_animation", "animation": name,
                            "parts": group.get("members", []), "motion": "spin",
                            "axis": registry.get("rotation_axis_world") or [0, 1, 0],
                            "cycles_per_loop": 1, "frames": 120, "startAt": 0})
        if action == "verify" and step.get("spec"):
            actions.append({"type": "show_panel", "component": "ExplanationCard",
                            "anchor": "screen-top-right",
                            "props": {"title": step.get("title", "Check"),
                                      "body": step.get("spec", "")},
                            "startAt": 0})

    # Static focus steps → dim + highlight + label (+ arrow / pulse)
    elif action in ("mount", "connect", "clean", "remove", "press", "none"):
        if resolved:
            actions.append({"type": "dim_others", "except": resolved,
                            "factor": dim_factor, "startAt": 0})
            actions.append({"type": "highlight", "target": resolved[0],
                            "color": INDIGO, "intensity": 1.0, "startAt": 0})
            actions.append({"type": "label", "target": resolved[0],
                            "text": primary_text,
                            "kicker": action.upper(), "anchor": "auto", "startAt": 0})
            if action == "press":
                actions.append({"type": "pulse", "target": resolved[0],
                                "cycles": 2, "startAt": 0})
            if action in ("connect", "remove") and len(resolved) >= 2:
                actions.append({"type": "arrow", "from": resolved[0],
                                "to": resolved[1], "color": GREEN,
                                "style": "solid", "startAt": 0})
        else:
            actions.append({"type": "label", "target": _any_target(registry),
                            "text": step.get("title", "Step"),
                            "kicker": action.upper(), "anchor": "auto", "startAt": 0})

    # Final beat: reset + hero + signature motion
    if is_last:
        actions.append({"type": "reset", "scope": "all", "startAt": 0})
        actions.append({"type": "move_camera", "to": {"preset": hero},
                        "ease": "easeInOut", "duration": 0.9, "startAt": 0})
        sig = _signature_clip(anim_lib)
        if sig:
            actions.append({"type": "play_animation", "animation": sig,
                            "from": 0, "to": 1, "rate": 1.0, "loop": True, "startAt": 0})

    return _beat(step, narration, duration, actions), bakes


def _beat(step, narration, duration, actions):
    return {"id": _slug(step.get("title")), "narration": narration,
            "duration": duration, "actions": actions}


def _any_target(registry):
    aliases = registry.get("aliases", {})
    if aliases:
        v = next(iter(aliases.values()))
        return v if isinstance(v, str) else next(iter(aliases.keys()))
    parts = registry.get("parts", {})
    return next(iter(parts.keys())) if parts else "mesh1"


def _signature_clip(anim_lib):
    anims = list((anim_lib.get("animations", {}) or {}).keys())
    # prefer a loopable "spin"/"rotation" clip
    for a in anims:
        if "spin" in a or "rotation" in a:
            return a
    return anims[0] if anims else None


# ---------------------------------------------------------------------------
# Generative staging — assembly beats over a freshly-built asset
# ---------------------------------------------------------------------------

def _part_label_text(pid, parts_meta):
    meta = parts_meta.get(pid, {})
    al = meta.get("aliases") or []
    if al:
        return al[0].title()
    return pid.replace("_", " ").title()


def _stage_generative(segment, registry):
    """Stage a generated flat-pack asset's steps into assembly beats.

    Beat vocabulary differs from the curated path: instead of play_animation
    on a driven group, we EXPLODE the unit on the identify beat and ASSEMBLE
    the relevant parts on each build step — the manual literally builds itself
    part-by-part on screen. Fastening / finishing steps highlight seated parts.
    """
    hints = registry.get("director_hints", {})
    hero = (hints.get("preferred_camera_presets") or ["hero_threequarter"])[0]
    parts_meta = registry.get("parts", {})
    order = (registry.get("assembly", {}) or {}).get("order", []) or list(parts_meta)
    steps = segment.get("steps", [])
    n = len(steps)
    beats = []

    for i, step in enumerate(steps):
        is_first = (i == 0)
        is_last = (i == n - 1)
        action = (step.get("action") or "none").lower()
        assembles = [p for p in (step.get("assembles") or []) if p in parts_meta]

        narration = (step.get("instruction") or step.get("title") or "").strip()
        if step.get("spec"):
            narration = f"{narration}  ({step['spec']})".strip()
        if step.get("warning"):
            narration = f"{narration}  ⚠ {step['warning']}".strip()
        words = max(1, len(narration.split()))

        actions = []
        assemble_dur = 1.0

        # Identify / first beat: explode the whole unit so every part reads.
        if is_first or action == "identify":
            actions.append({"type": "move_camera", "to": {"preset": hero},
                            "ease": "easeInOut", "duration": 1.0, "startAt": 0})
            actions.append({"type": "explode", "scope": "all", "startAt": 0})
            for pid in (order[:3] or list(parts_meta)[:3]):
                actions.append({"type": "label", "target": pid,
                                "text": _part_label_text(pid, parts_meta),
                                "kicker": "PART", "anchor": "auto", "startAt": 0})
            duration = max(4.5, round(words / 12.0 + 1.5, 1))
            beats.append(_beat(step, narration, duration, actions))
            continue

        # Build step that seats new parts → assemble them into place.
        if assembles:
            actions.append({"type": "assemble", "parts": assembles,
                            "duration": assemble_dur, "startAt": 0})
            actions.append({"type": "dim_others", "except": assembles,
                            "factor": 0.5, "startAt": 0})
            # Label after the parts settle (anchors at the seated position).
            actions.append({"type": "label", "target": assembles[0],
                            "text": step.get("title", _part_label_text(assembles[0], parts_meta)),
                            "kicker": action.upper().replace("_", " "),
                            "anchor": "auto", "startAt": assemble_dur})
        else:
            # Fastening / finishing step → highlight seated target parts.
            pairs = _resolve_pairs(step.get("target_parts", []), registry)
            resolved = [t for t, _ in pairs]
            if resolved:
                actions.append({"type": "dim_others", "except": resolved,
                                "factor": 0.5, "startAt": 0})
                actions.append({"type": "highlight", "target": resolved[0],
                                "color": HEAT if action == "verify" else INDIGO,
                                "intensity": 1.0, "startAt": 0})
                actions.append({"type": "label", "target": resolved[0],
                                "text": step.get("title", "Step"),
                                "kicker": action.upper(), "anchor": "auto", "startAt": 0})
                if action in ("press", "connect"):
                    actions.append({"type": "pulse", "target": resolved[0],
                                    "cycles": 2, "startAt": 0})
                if action == "connect" and len(resolved) >= 2:
                    actions.append({"type": "arrow", "from": resolved[0],
                                    "to": resolved[1], "color": GREEN,
                                    "style": "solid", "startAt": 0})
            else:
                actions.append({"type": "label", "target": _any_target(registry),
                                "text": step.get("title", "Step"),
                                "kicker": action.upper(), "anchor": "auto", "startAt": 0})

        if is_last:
            actions.append({"type": "assemble", "scope": "all",
                            "duration": 0.8, "startAt": 0})
            actions.append({"type": "reset", "scope": "highlights", "startAt": 0})
            actions.append({"type": "move_camera", "to": {"preset": hero},
                            "ease": "easeInOut", "duration": 1.0, "startAt": 0})

        base = words / 12.0 + 1.5
        duration = max(4.5, round(max(base, assemble_dur + 2.0), 1)) if assembles \
            else max(4.0, round(base, 1))
        beats.append(_beat(step, narration, duration, actions))

    return beats


def build_walkthrough_generate(manual_text: str, *, segment: dict | None = None,
                               build_timeout: int = 600) -> dict:
    """Generative path: SEGMENT the manual → BUILD the asset part-by-part in
    Blender → register it → stage assembly beats. Never reuses a curated model.
    """
    segment_manual, build_and_register = _segment_fns()
    seg = segment or segment_manual(manual_text)

    out = build_and_register(seg, timeout=build_timeout)
    asset = out["asset"]
    registry = json.loads(Path(out["registry_path"]).read_text(encoding="utf-8"))
    hints = registry.get("director_hints", {})
    background = hints.get("background_default", "#F5F4EF")

    beats = _stage_generative(seg, registry)
    steps = seg.get("steps", [])

    contract = {
        "meta": {
            "id": _slug(seg.get("title", asset.asset_id)),
            "mode": "usermanualXR",
            "build_mode": "generate",
            "asset_id": asset.asset_id,
            "asset": asset.glb,
            "camera_presets": registry.get("camera_presets", {}),
            "generatedBy": "walkthrough-generative-v1",
            "generatedAt": _now_iso(),
            "schemaVersion": "0.1.0",
            "manual_product_kind": seg.get("product_kind"),
            "segment_source": seg.get("_source"),
            "n_parts": out["result"].get("n_parts"),
            "step_count": len(beats),
        },
        "explanation": {
            "title": seg.get("title", f"{asset.kind} Walkthrough"),
            "summary": f"A step-by-step XR walkthrough of {asset.kind}, BUILT "
                       f"part-by-part from its manual ({out['result'].get('n_parts')} "
                       f"parts, {len(beats)} steps).",
        },
        "scene": {
            "camera": {"preset": (hints.get("preferred_camera_presets") or ["hero_threequarter"])[0]},
            "environment": {"background": background},
            "assembly": registry.get("assembly", {}),
        },
        "beats": beats,
        "steps_index": [
            {"n": s.get("n", i + 1), "title": s.get("title"),
             "beat_id": beats[i]["id"] if i < len(beats) else None}
            for i, s in enumerate(steps)
        ],
    }

    return {
        "ok": True,
        "build_mode": "generate",
        "asset_id": asset.asset_id,
        "asset_glb": asset.glb,
        "camera_presets": registry.get("camera_presets", {}),
        "manual_plan": seg,
        "build_result": {k: out["result"].get(k) for k in
                         ("n_parts", "bbox_m", "elapsed_s")},
        "match": {"asset_id": asset.asset_id, "kind": asset.kind,
                  "reason": "generated from manual (no curated match needed)"},
        "contract": contract,
    }


# ---------------------------------------------------------------------------
# Top-level build
# ---------------------------------------------------------------------------

def build_walkthrough(manual_text: str, *, mode: str = "match",
                      run_bake: bool = True) -> dict:
    """Build an XR walkthrough from a manual.

    mode="match"    : classify + match a CURATED library model, stage its steps
                      (the original path; used by the legacy explainer drawer).
    mode="generate" : SEGMENT the manual, BUILD the asset part-by-part, stage
                      the assembly (the manual->XR mini-app path). Never
                      collapses onto a curated model.
    mode="auto"     : alias for "generate".
    """
    if mode in ("generate", "auto"):
        return build_walkthrough_generate(manual_text)
    return _build_walkthrough_match(manual_text, run_bake=run_bake)


def _build_walkthrough_match(manual_text: str, *, run_bake: bool = True) -> dict:
    plan = ingest_manual(manual_text)
    match = lib.match_product(plan.get("product_kind", ""),
                              plan.get("product_keywords", []))
    if match.asset is None:
        return {"ok": False, "error": match.reason,
                "manual_plan": plan,
                "match": {"asset_id": None, "reason": match.reason}}

    asset = match.asset
    registry = json.loads(lib.registry_path(asset).read_text(encoding="utf-8"))
    anim_lib = json.loads(lib.animation_library_path(asset).read_text(encoding="utf-8"))
    hints = registry.get("director_hints", {})
    background = hints.get("background_default", "#F5F4EF")

    steps = plan.get("steps", [])
    beats = []
    for i, step in enumerate(steps):
        beat, _bakes = _stage_step(
            step, registry, anim_lib, hints,
            is_first=(i == 0), is_last=(i == len(steps) - 1),
        )
        beats.append(beat)

    contract = {
        "meta": {
            "id": _slug(plan.get("title", asset.asset_id)),
            "mode": "usermanualXR",
            "asset_id": asset.asset_id,
            "asset": asset.glb,
            "generatedBy": "walkthrough-director-v1",
            "generatedAt": _now_iso(),
            "schemaVersion": "0.1.0",
            "manual_product_kind": plan.get("product_kind"),
            "step_count": len(beats),
        },
        "explanation": {
            "title": plan.get("title", f"{asset.kind} Walkthrough"),
            "summary": f"A step-by-step XR walkthrough of {asset.kind}, "
                       f"generated from its user manual ({len(beats)} steps).",
        },
        "scene": {
            "camera": {"preset": (hints.get("preferred_camera_presets") or ["hero_threequarter"])[0]},
            "environment": {"background": background},
        },
        "beats": beats,
        "steps_index": [
            {"n": s.get("n", i + 1), "title": s.get("title"),
             "beat_id": beats[i]["id"] if i < len(beats) else None}
            for i, s in enumerate(steps)
        ],
    }

    # Bake any motions the model lacks (reuses the existing bake bridge)
    if run_bake and maybe_bake_from_contract is not None:
        try:
            baked = maybe_bake_from_contract(contract)
            if baked:
                contract["meta"]["baked_clips"] = list(baked.keys())
        except Exception as e:
            contract["meta"]["_bake_error"] = str(e)

    return {
        "ok": True,
        "asset_id": asset.asset_id,
        "asset_glb": asset.glb,
        "manual_plan": plan,
        "match": {"asset_id": asset.asset_id, "kind": asset.kind,
                  "score": match.score, "reason": match.reason},
        "contract": contract,
    }


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
