"""design_system.py — the SPATAIL Placement Design System (executable policy).

Authoritative spec: docs/spatail-placement-design-system.md (owner-supplied, 2026-06-10).
One-sentence product rule:

    SPATAIL places digital objects according to what the user is trying to understand
    or do, using real-world surfaces, correct scale, comfortable positioning, and
    meaningful interactions to turn 3D content into useful spatial experiences.

This module is the PC-side half of the system: it converts the brain's understanding
(domain/intent/subject + asset footprints + stream) into the §12 PLACEMENT CONTRACT —
pure POLICY (anchor type, scale mode, interactions, comfort, fallbacks), never hard
coordinates. The on-device placement solver resolves the policy against the live
RoomModel at runtime (geometry). Deterministic + offline, like the rest of the studio.

Decision hierarchy implemented (spec §2):
    1 user intent → 2 object category → 3 placement mode/preset → 4 anchor →
    5 scale → 6 position → 7 orientation → 8 interaction → 9 comfort → 10 fallback
"""
from __future__ import annotations

# ── §7 Comfort: distance zones ──────────────────────────────────────────────
ZONES = {
    "near": {"range": "0.3m-0.8m", "use": "grab/rotate/inspect"},
    "work": {"range": "0.8m-1.5m", "use": "tabletop learning, repair guides"},
    "view": {"range": "1.5m-3m",   "use": "large objects, wall content, comparison"},
    "room": {"range": "3m+",       "use": "architecture, vehicles, room simulations"},
}

# ── §5 Scale modes ──────────────────────────────────────────────────────────
SCALE_MODES = ("true_scale", "fit_to_surface", "miniature", "enlarged_detail", "adaptive")

# Real-footprint thresholds (metres, max dimension) that trigger scale adjustment.
TOO_LARGE_FOR_TABLE_M = 1.2     # bigger than this can't sit on a table unscaled
TOO_LARGE_FOR_ROOM_M = 2.5      # bigger than this won't fit most rooms at true scale
HAND_INSPECTABLE_MIN_M = 0.08   # smaller than this should be enlarged to inspect

# ── §10 Fallback chains (always provide a fallback) ─────────────────────────
FALLBACKS = {
    "table": ["floor", "user_volume", "rescan"],
    "floor": ["table", "user_volume", "rescan"],
    "wall":  ["floating_panel", "floor_stand", "table"],
    "room":  ["miniature_mode", "fit_to_surface", "preview_2d"],
    "object": ["world"],            # tracked overlay degrades to world-anchored
    "user":  [],
}

# ── §11 Presets (§3 placement modes made concrete) ──────────────────────────
# Each preset is a complete §12 contract template. decide_placement() picks one
# and adjusts scale/notes from the real asset footprint.
PRESETS: dict[str, dict] = {
    "tabletop_learning_model": {
        "mode": "miniature",                       # §3.2 on a table
        "anchor": {"type": "table", "fallback": "floor"},
        "scale": {"mode": "fit_to_surface", "maxSurfaceCoverage": 0.65,
                  "showScaleReference": True},
        "position": {"alignment": "centered_on_surface", "faceUser": True,
                     "avoidOcclusion": True},
        "interaction": {"canMove": True, "canRotate": True, "canScale": True,
                        "semanticActions": ["rotate", "scale", "isolate_part",
                                            "highlight_part", "animate", "reset"]},
        "ui": {"labelBehavior": "on_select", "panelPosition": "beside_object",
               "textMode": "flat_user_facing"},
        "comfort": {"preferredDistance": ZONES["work"]["range"],
                    "avoidForcedHeadTurn": True, "allowRecentering": True},
        "fallback": {"ifNoTable": "use_floor", "ifRoomTooSmall": "miniature_mode",
                     "ifTrackingLow": "request_rescan"},
    },
    "true_scale_product_preview": {
        "mode": "real_scale",                      # §3.1
        "anchor": {"type": "floor", "fallback": "table"},
        "scale": {"mode": "true_scale", "maxSurfaceCoverage": 1.0,
                  "showScaleReference": False},
        "position": {"alignment": "in_front_of_user", "faceUser": True,
                     "avoidOcclusion": True, "groundWithShadow": True,
                     "leaveWalkAroundSpace": True},
        "interaction": {"canMove": True, "canRotate": True, "canScale": False,
                        "semanticActions": ["move", "rotate", "measure",
                                            "compare", "reset"]},
        "ui": {"labelBehavior": "on_select", "panelPosition": "beside_object",
               "textMode": "flat_user_facing"},
        "comfort": {"preferredDistance": ZONES["view"]["range"],
                    "avoidForcedHeadTurn": True, "allowRecentering": True},
        "fallback": {"ifNoFloor": "use_table_miniature",
                     "ifRoomTooSmall": "miniature_mode",
                     "ifTrackingLow": "request_rescan"},
    },
    "mechanical_exploded_view": {
        "mode": "exploded",                        # §3.3
        "anchor": {"type": "table", "fallback": "floor"},
        "scale": {"mode": "fit_to_surface", "maxSurfaceCoverage": 0.65,
                  "showScaleReference": True},
        "position": {"alignment": "centered_on_surface", "faceUser": True,
                     "avoidOcclusion": True},
        "interaction": {"canMove": True, "canRotate": True, "canScale": True,
                        "semanticActions": ["explode", "collapse", "isolate_part",
                                            "highlight_part", "cutaway",
                                            "step_sequence", "reset"]},
        "ui": {"labelBehavior": "on_select", "panelPosition": "beside_object",
               "textMode": "flat_user_facing"},
        "comfort": {"preferredDistance": ZONES["work"]["range"],
                    "avoidForcedHeadTurn": True, "allowRecentering": True},
        "fallback": {"ifNoTable": "use_floor", "ifRoomTooSmall": "miniature_mode",
                     "ifTrackingLow": "request_rescan"},
    },
    "object_anchored_repair_guide": {
        "mode": "object_overlay",                  # §3.4 — the TRACKED stream
        "anchor": {"type": "object", "fallback": "world"},
        "scale": {"mode": "true_scale", "maxSurfaceCoverage": 1.0,
                  "showScaleReference": False},
        "position": {"alignment": "aligned_to_object", "faceUser": False,
                     "avoidOcclusion": True,        # never cover the target area
                     "panelsOffsetFromTarget": True},
        "interaction": {"canMove": False, "canRotate": False, "canScale": False,
                        "semanticActions": ["next_step", "previous_step",
                                            "confirm_step", "highlight_target",
                                            "show_tool", "show_ghost_part",
                                            "re_align"]},
        "ui": {"labelBehavior": "current_step_only", "panelPosition": "beside_object",
               "textMode": "flat_user_facing"},
        "comfort": {"preferredDistance": ZONES["work"]["range"],
                    "avoidForcedHeadTurn": True, "allowRecentering": False},
        "fallback": {"ifTrackingLost": "pause_and_guide",
                     "ifTrackingLow": "request_realign",
                     "ifNoReferenceObject": "world_anchor_near_object"},
    },
    "wall_explanation_board": {
        "mode": "wall_board",                      # §3.5
        "anchor": {"type": "wall", "fallback": "floating_panel"},
        "scale": {"mode": "fit_to_surface", "maxSurfaceCoverage": 0.7,
                  "showScaleReference": False},
        "position": {"alignment": "flush_on_wall_eye_level", "faceUser": True,
                     "avoidOcclusion": True},
        "interaction": {"canMove": True, "canRotate": False, "canScale": True,
                        "semanticActions": ["expand", "collapse", "annotate",
                                            "pin", "step_through"]},
        "ui": {"labelBehavior": "progressive", "panelPosition": "on_board",
               "textMode": "flat_user_facing"},
        "comfort": {"preferredDistance": ZONES["view"]["range"],
                    "avoidForcedHeadTurn": True, "allowRecentering": True},
        "fallback": {"ifNoWall": "floating_panel", "ifRoomTooSmall": "fit_to_surface",
                     "ifTrackingLow": "request_rescan"},
    },
    "room_scale_simulation": {
        "mode": "room_simulation",                 # §3.6
        "anchor": {"type": "room", "fallback": "floor"},
        "scale": {"mode": "adaptive", "maxSurfaceCoverage": 0.8,
                  "showScaleReference": True},
        "position": {"alignment": "main_action_in_front", "faceUser": True,
                     "avoidOcclusion": True, "leaveWalkAroundSpace": True,
                     "avoidBehindUser": True},
        "interaction": {"canMove": False, "canRotate": False, "canScale": False,
                        "semanticActions": ["play", "pause", "scrub_timeline",
                                            "change_parameter", "walk_around",
                                            "reset"]},
        "ui": {"labelBehavior": "progressive", "panelPosition": "user_relative_temporary",
               "textMode": "flat_user_facing"},
        "comfort": {"preferredDistance": ZONES["room"]["range"],
                    "avoidForcedHeadTurn": True, "allowRecentering": True},
        "fallback": {"ifRoomTooSmall": "miniature_mode", "ifNoFloor": "use_table",
                     "ifTrackingLow": "request_rescan"},
    },
}

# ── §2 step 2: object category from the engine's domain (+ subject keywords) ─
# Engine domains: mechanical biological scientific architectural historical product
#                 manufacturing data_visualization educational general_knowledge
_ROOM_SIM_HINTS = ("gravity", "traffic", "weather", "orbit around the room",
                   "ecosystem", "flow through the room", "interior", "room layout")
_BOARD_HINTS = ("diagram", "timeline", "poster", "chart", "graph", "explanation board",
                "flowchart", "map of", "infographic")
_LIFESIZE_HINTS = ("chair", "sofa", "table", "tv", "television", "fridge",
                   "refrigerator", "couch", "desk", "lamp", "appliance", "speaker",
                   "monitor", "skeleton", "human body", "mannequin")


def _first_hint(text: str, hints) -> str | None:
    return next((k for k in hints if k in text), None)


def categorize(understanding: dict, trace: list | None = None) -> str:
    """§2 step 2 — object category (engine domain refined by subject keywords).
    Appends a human-readable line per fired rule to `trace` (spec §2.3)."""
    t = trace if trace is not None else []
    domain = (understanding.get("domain") or "general_knowledge").lower()
    subject = (understanding.get("subject") or "").lower()
    summary = (understanding.get("summary") or "").lower()
    text = f"{subject} {summary}"
    hint = _first_hint(text, _BOARD_HINTS)
    if hint:
        t.append(f"hint '{hint}' -> board")
        return "board"
    hint = _first_hint(text, _ROOM_SIM_HINTS)
    if hint:
        t.append(f"hint '{hint}' -> room_process")
        return "room_process"
    hint = _first_hint(text, _LIFESIZE_HINTS)
    if domain == "product" or hint:
        why = " + ".join((["domain=product"] if domain == "product" else [])
                         + ([f"hint '{hint}'"] if hint else []))
        t.append(f"{why} -> lifesize_product")
        return "lifesize_product"
    if domain == "mechanical" or domain == "manufacturing":
        t.append(f"domain={domain} -> mechanical")
        return "mechanical"
    if domain == "architectural":
        t.append("domain=architectural -> large_structure")
        return "large_structure"
    if domain == "biological":
        t.append("domain=biological -> organism_or_part")
        return "organism_or_part"
    if domain == "data_visualization":
        t.append("domain=data_visualization -> board")
        return "board"
    t.append(f"domain={domain}, no category hint -> learning_object")
    return "learning_object"


def choose_preset(understanding: dict, *, tracked: bool = False,
                  trace: list | None = None) -> str:
    """§2 steps 1–3 — intent + category → placement mode (a preset id)."""
    t = trace if trace is not None else []
    if tracked:
        # The TRACKED stream: a real object is the anchor. Placement mode is the
        # object-anchored overlay regardless of category (spec §3.4).
        t.append("stream=tracked (real object is the anchor, §3.4) "
                 "-> object_anchored_repair_guide")
        return "object_anchored_repair_guide"
    intent = (understanding.get("intent") or "explain").lower()
    cat = categorize(understanding, trace=t)
    if cat == "board":
        t.append("category=board -> wall_explanation_board")
        return "wall_explanation_board"
    if cat == "room_process":
        t.append("category=room_process -> room_scale_simulation")
        return "room_scale_simulation"
    if cat == "lifesize_product" and intent in ("preview", "compare", "present", "explore"):
        t.append(f"category=lifesize_product + intent '{intent}' "
                 "-> true_scale_product_preview")
        return "true_scale_product_preview"
    if cat == "lifesize_product":
        # learning about a product (not judging fit) still reads best at true scale
        t.append(f"category=lifesize_product (intent '{intent}' still reads best at "
                 "true scale) -> true_scale_product_preview")
        return "true_scale_product_preview"
    if cat == "mechanical" and intent in ("explain", "learn", "analyze", "assemble",
                                          "demonstrate", "explore"):
        t.append(f"category=mechanical + intent '{intent}' -> mechanical_exploded_view")
        return "mechanical_exploded_view"
    if cat == "large_structure":
        t.append("category=large_structure -> tabletop_learning_model (miniature, §3.2)")
        return "tabletop_learning_model"        # §3.2 miniature on the table
    t.append(f"category={cat} + intent '{intent}' -> tabletop_learning_model")
    return "tabletop_learning_model"


def _primary_footprint_m(assets: list[dict] | None) -> float:
    """Max real dimension (m) of the primary asset, 0.0 when unknown."""
    if not assets:
        return 0.0
    primary = next((a for a in assets if a.get("role") == "primary_object"), assets[0])
    dims = primary.get("scaleMeters") or []
    try:
        return float(max(dims)) if dims else 0.0
    except (TypeError, ValueError):
        return 0.0


def _adjust_scale(contract: dict, footprint_m: float, trace: list | None = None) -> None:
    """§2 step 5 / §5 — refine the preset's scale mode with the REAL footprint.
    Mutates contract['scale'] (and notes). Usability first, realism second."""
    t = trace if trace is not None else []
    scale = contract["scale"]
    mode = scale["mode"]
    if footprint_m <= 0.0:
        t.append(f"no real footprint -> keep preset scale mode '{mode}'")
        return
    # §5 Enlarged Detail: tiny things become hand-inspectable — and say so.
    if footprint_m < HAND_INSPECTABLE_MIN_M and mode != "true_scale":
        scale["mode"] = "enlarged_detail"
        scale["showScaleReference"] = True
        scale["scaleNote"] = "Enlarged for inspection — not actual size."
        t.append(f"footprint {footprint_m:.2f}m < HAND_INSPECTABLE_MIN_M "
                 f"{HAND_INSPECTABLE_MIN_M} -> enlarged_detail")
        return
    # §5 Miniature: too big for the anchor surface → intentional reduction + note.
    anchor = contract["anchor"]["type"]
    if mode == "true_scale":
        if footprint_m > TOO_LARGE_FOR_ROOM_M:
            scale["mode"] = "miniature"
            scale["showScaleReference"] = True
            ratio = max(2, round(footprint_m / 0.6))
            scale["scaleNote"] = (f"Scaled to about 1:{ratio} — too large for the "
                                  f"current room at true scale.")
            t.append(f"footprint {footprint_m:.1f}m > TOO_LARGE_FOR_ROOM_M "
                     f"{TOO_LARGE_FOR_ROOM_M} -> miniature 1:{ratio}")
        else:
            t.append(f"footprint {footprint_m:.2f}m fits the room -> keep true_scale")
        return
    if anchor == "table" and footprint_m > TOO_LARGE_FOR_TABLE_M:
        scale["mode"] = "miniature"
        scale["showScaleReference"] = True
        ratio = max(2, round(footprint_m / 0.5))
        scale["scaleNote"] = f"Scaled to about 1:{ratio} for tabletop view."
        t.append(f"footprint {footprint_m:.1f}m > TOO_LARGE_FOR_TABLE_M "
                 f"{TOO_LARGE_FOR_TABLE_M} -> miniature 1:{ratio}")
        return
    t.append(f"footprint {footprint_m:.2f}m within '{mode}' limits -> keep {mode}")


def decide_anchoring(*, tracked: bool, reference_object_url: str | None = None,
                     object_id: str | None = None) -> dict:
    """The stream split the runtime mounts the experience root on.

    mode "object": ARObjectAnchor from a .referenceobject (iOS 27 object tracking;
                   high-rate `trackingObjects` for handheld, `detectionObjects` else).
    mode "world":  raycast world anchor resolved by the on-device placement solver.
    Tracked always degrades gracefully to world (spec §10)."""
    if tracked:
        return {
            "mode": "object",
            "objectId": object_id or "",
            "referenceObjectUrl": reference_object_url or "",
            "tracking": "detection",          # "tracking" (high-rate) for handheld
            "pauseOnTrackingLoss": True,      # §3.4: pause if tracking is lost
            "fallback": "world",
        }
    return {"mode": "world", "fallback": "world"}


def decide_placement(understanding: dict, assets: list[dict] | None = None, *,
                     tracked: bool = False, reference_object_url: str | None = None,
                     tracked_object_id: str | None = None) -> dict:
    """§2 hierarchy, end to end: understanding (+ real footprints, + stream) →
    the §12 placement contract. Policy only — the device solver does geometry.
    contract['decisionTrace'] records which hint/threshold fired at each step
    (spec §2.3: placement.designSystem.decisionTrace)."""
    trace: list = []
    preset_id = choose_preset(understanding, tracked=tracked, trace=trace)
    import copy
    contract = copy.deepcopy(PRESETS[preset_id])
    contract["intent"] = preset_id
    contract["preset"] = preset_id
    contract["zones"] = ZONES
    footprint_m = _primary_footprint_m(assets)
    if assets and footprint_m > 0.0:
        primary = next((a for a in assets if a.get("role") == "primary_object"), assets[0])
        src = primary.get("footprintSource")
        trace.append(f"primary footprint {footprint_m:.2f}m"
                     + (f" (source={src})" if src else ""))
    _adjust_scale(contract, footprint_m, trace=trace)
    contract["anchoring"] = decide_anchoring(
        tracked=tracked, reference_object_url=reference_object_url,
        object_id=tracked_object_id)
    trace.append(f"anchoring.mode={contract['anchoring']['mode']} "
                 f"(fallback={contract['anchoring']['fallback']})")
    contract["decisionTrace"] = trace
    return contract


# ── selftest ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    import sys

    CASES = [
        # (understanding, assets, tracked, expected preset, expected anchor)
        ({"domain": "product", "intent": "preview", "subject": "a lounge chair",
          "summary": "see if this chair fits my room"},
         [{"id": "chair", "role": "primary_object", "scaleMeters": [0.7, 0.9, 0.7]}],
         False, "true_scale_product_preview", "floor"),
        ({"domain": "mechanical", "intent": "explain", "subject": "v8 engine",
          "summary": "how does a v8 engine work"},
         [{"id": "engine", "role": "primary_object", "scaleMeters": [0.9, 0.7, 0.7]}],
         False, "mechanical_exploded_view", "table"),
        ({"domain": "scientific", "intent": "learn", "subject": "solar system",
          "summary": "the solar system"},
         [{"id": "sun", "role": "primary_object", "scaleMeters": [1.4, 1.4, 1.4]}],
         False, "tabletop_learning_model", "table"),
        ({"domain": "biological", "intent": "explain", "subject": "photosynthesis diagram",
          "summary": "a photosynthesis diagram"},
         None, False, "wall_explanation_board", "wall"),
        ({"domain": "scientific", "intent": "demonstrate", "subject": "gravity",
          "summary": "show gravity pulling an apple in my room"},
         None, False, "room_scale_simulation", "room"),
        ({"domain": "mechanical", "intent": "explain", "subject": "trash can",
          "summary": "how does this trash can work"},
         None, True, "object_anchored_repair_guide", "object"),
        ({"domain": "biological", "intent": "explain", "subject": "red blood cell",
          "summary": "how a red blood cell carries oxygen"},
         [{"id": "cell", "role": "primary_object", "scaleMeters": [0.02, 0.02, 0.01]}],
         False, "tabletop_learning_model", "table"),
        ({"domain": "architectural", "intent": "learn", "subject": "suspension bridge",
          "summary": "how a suspension bridge stands"},
         [{"id": "bridge", "role": "primary_object", "scaleMeters": [4.0, 1.0, 0.8]}],
         False, "tabletop_learning_model", "table"),
    ]

    failures = 0
    for u, assets, tracked, want_preset, want_anchor in CASES:
        c = decide_placement(u, assets, tracked=tracked)
        ok = c["preset"] == want_preset and c["anchor"]["type"] == want_anchor
        # invariants (spec §10 + §12): every contract has a fallback + comfort + ui
        ok = ok and bool(c["fallback"]) and bool(c["comfort"]) and bool(c["ui"])
        ok = ok and c["anchoring"]["mode"] == ("object" if tracked else "world")
        # spec §2.3: every contract carries a non-empty human-readable decisionTrace
        ok = ok and bool(c.get("decisionTrace")) \
            and all(isinstance(s, str) and s for s in c["decisionTrace"])
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        extra = c["scale"].get("scaleNote", "")
        print(f"  [{status}] {u['subject']!r} -> {c['preset']} / anchor={c['anchor']['type']}"
              f" / scale={c['scale']['mode']}" + (f" ({extra})" if extra else ""))

    # tiny cell must be enlarged; huge bridge must be miniature with a note
    cell = decide_placement(CASES[6][0], CASES[6][1])
    assert cell["scale"]["mode"] == "enlarged_detail", cell["scale"]
    bridge = decide_placement(CASES[7][0], CASES[7][1])
    assert bridge["scale"]["mode"] == "miniature" and bridge["scale"].get("scaleNote"), bridge["scale"]
    # tracked contract must pause on tracking loss and fall back to world
    t = decide_placement(CASES[5][0], None, tracked=True)
    assert t["anchoring"]["pauseOnTrackingLoss"] and t["anchoring"]["fallback"] == "world"
    # decisionTrace must exist and NAME the threshold/hint that fired (spec §2.3)
    assert any("TOO_LARGE_FOR_TABLE_M" in s for s in bridge["decisionTrace"]), bridge["decisionTrace"]
    assert any("HAND_INSPECTABLE_MIN_M" in s for s in cell["decisionTrace"]), cell["decisionTrace"]
    assert any("stream=tracked" in s for s in t["decisionTrace"]), t["decisionTrace"]
    chair = decide_placement(CASES[0][0], CASES[0][1])
    assert any("hint 'chair'" in s and "lifesize_product" in s
               for s in chair["decisionTrace"]), chair["decisionTrace"]
    assert any(s.startswith("anchoring.mode=") for s in chair["decisionTrace"])

    if failures:
        print(f"SELFTEST FAIL ({failures})")
        sys.exit(1)
    print("SELFTEST PASS")
    print(json.dumps(decide_placement(CASES[1][0], CASES[1][1]), indent=2)[:1200])
