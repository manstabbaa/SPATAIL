"""experience.py — assemble the modular SPATAIL experience contract.

Ties the existing brain to the new agent director:
  understanding  <- RepresentationEngine (domain/intent/subject)         [reuse]
  assets         <- AssetLibrary.resolve per requested asset             [reuse]
  beats          <- director.compose (agent picks mechanics freely)      [new]
  stage          <- plan.placement (xr_design comfort)                   [reuse]

Emits schemaVersion 0.5.0-spatail-modular — platform-agnostic. No rigid presentation
template: the beats are whatever the director designed from the mechanics catalog.
Works for pasted text or an image-derived brief (kind="text"|"image").
"""
from __future__ import annotations

import sys
from pathlib import Path

_STUDIO = Path(__file__).resolve().parents[1]
for p in (_STUDIO, _STUDIO / "server", _STUDIO / "mechanics", _STUDIO / "director"):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

import composer  # noqa: E402
import asset_brief as abrief  # noqa: E402  (the story→asset requirements contract)
from representation.engine import RepresentationEngine  # noqa: E402
from spatail.design_system import decide_placement  # noqa: E402  (Placement Design System)

SCHEMA = "0.7.0-spatail-modular"
_ROLE = {"hero": "primary_object", "primary": "primary_object", "part": "part",
         "comparison_item": "comparison_object", "comparison": "comparison_object",
         "environment": "environment", "label_target": "label"}


def _load_library():
    try:
        from library.asset_library import AssetLibrary
        lib = AssetLibrary()
        return lib if lib.count else None
    except Exception:
        return None


def _load_manifest(asset_id: str) -> dict:
    """Read {asset_id}_manifest.json (the Blender->SPATAIL contract: parts + clips +
    anchors) from the gen artifacts dir if a build produced one."""
    try:
        import json
        root = Path(__file__).resolve().parents[2]          # SPATAIL_MAX
        mf = root / "studio" / "out" / "gen" / f"{asset_id}_manifest.json"
        if asset_id and mf.exists():
            return json.loads(mf.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


def _resolve_assets(plan, manifest, lib) -> list[dict]:
    assets, seen, seen_models = [], set(), set()
    reqs = manifest.assetRequests or []
    for req in reqs:
        if req.assetId in seen:
            continue
        seen.add(req.assetId)
        meta, res = {}, None
        if lib is not None:
            res = lib.resolve(asset_id=req.assetId, subject=plan.subject, domain=plan.domain,
                              semantic_role=req.semanticRole, strategy=plan.strategy)
            meta = lib.assets.get(res.libraryAssetId, {}) if res.libraryAssetId else {}
        a = {
            "id": req.assetId,
            "name": meta.get("name", req.assetId.replace("_", " ").title()),
            "role": _ROLE.get(req.semanticRole, "part"),
            "description": req.description,
            "glbUrl": getattr(res, "glbPath", "") if res else "",
            "usdzUrl": getattr(res, "usdzPath", "") if res else "",
            "fallbackPrimitive": getattr(res, "fallbackPrimitive", "cube") if res else "cube",
            "scaleMeters": getattr(res, "scaleMeters", [0.2, 0.2, 0.2]) if res else [0.2, 0.2, 0.2],
            "supportsAnimation": bool(meta.get("supportsAnimation", False)),
            "supportsHighlight": bool(meta.get("supportsHighlight", False)),
            "supportsTransparency": bool(meta.get("supportsTransparency", False)),
            "source": getattr(res, "source", "generate") if res else "generate",
            "status": ("processed" if (res and res.source in ("library", "cached", "generated"))
                       else "placeholder"),
            "animation": None,        # filled by the mass asset+animation gen stage
        }
        # attach the Blender->SPATAIL contract (manifest parts + baked clips +
        # surface anchors) if a build produced one, so the sequencer can play the
        # asset's clips, the runtime can label / tap its parts, and the director
        # can pin steps to the anchors. Manifests are keyed by the PRODUCED asset
        # id — for library hits that's the resolved library id, so try it too.
        man = _load_manifest(a["id"]) or _load_manifest(
            getattr(res, "libraryAssetId", "") if res else "")
        if man.get("parts"):
            a["parts"] = man["parts"]
        if man.get("clips"):
            a["clips"] = man["clips"]
        if man.get("anchors"):
            a["anchors"] = man["anchors"]
        # story-driven addressable regions (slice 1): each is an overlay the runtime
        # lights up PRECISELY, so a step's effect lands on exactly that part.
        if man.get("regions"):
            a["regions"] = man["regions"]
        # Real-world scale contract (asset_service.produce → library): the runtime
        # branches on these so each asset renders at its true size — realScaleBaked
        # means the GLB is already metric (render at 1.0); realSizeMeters is the size
        # to fit the longest dim to when it isn't baked. Absent → tabletop footprint.
        if res is not None:
            if getattr(res, "realSizeMeters", None):
                a["realSizeMeters"] = list(res.realSizeMeters)
            if getattr(res, "realScaleBaked", False):
                a["realScaleBaked"] = True
        # Footprint provenance (Live Brain spec §2.3), so clients + traces can tell a
        # measured size from a guess: realSizeMeters only ever comes from the
        # object_size LLM estimate (asset_service.produce baked it into the library);
        # otherwise library/primitive hits carry catalog dims; anything else is the
        # default footprint guess.
        if res is not None and getattr(res, "realSizeMeters", None):
            a["footprintSource"] = "object_size_llm"
        elif res is not None and res.source in ("library", "primitive"):
            a["footprintSource"] = "library"
        else:
            a["footprintSource"] = "default_guess"
        # Drop assets that resolve to the SAME model as one already kept — the library
        # returns the whole object for unknown "parts", which would otherwise render as
        # N identical copies (e.g. "a v8 engine" -> 4 engine blocks). Real distinct
        # models (different usdz/glb) and to-be-generated placeholders (no model) stay.
        model_key = a["usdzUrl"] or a["glbUrl"]
        if model_key and model_key in seen_models:
            continue
        if model_key:
            seen_models.add(model_key)
        assets.append(a)
    return assets


def build_modular_experience(input_text: str, *, kind: str = "text", subject_hint: str | None = None,
                             summary: str | None = None, experience_id: str | None = None,
                             use_llm: bool = True, library=None,
                             tracked: bool = False, reference_object_url: str | None = None,
                             tracked_object_id: str | None = None) -> dict:
    eng = RepresentationEngine()
    plan, manifest = eng.run(subject_hint or input_text, experience_id=experience_id)
    lib = library if library is not None else _load_library()
    assets = _resolve_assets(plan, manifest, lib)

    understanding = {
        "domain": plan.domain, "intent": plan.intent, "subject": plan.subject,
        "summary": (summary or input_text or plan.subject).strip()[:600],
        "reasoning": plan.reasoning,
    }
    design = composer.compose(understanding, assets, use_llm=use_llm)

    # STORY→ASSET briefs (slice 1): from the composed sequence, derive — per asset —
    # which named parts the lesson points at / makes emissive, so a not-yet-generated
    # asset is BUILT to satisfy the story (anchors find those parts, regions bake them).
    # Threaded to the generation job by job_server; carried here for transparency.
    asset_requirements = {}
    for a in assets:
        b = abrief.build_brief(a["id"], sequence=design["sequence"], understanding=understanding)
        if abrief.has_requirements(b):
            asset_requirements[a["id"]] = b

    # Placement Design System (docs/spatail-placement-design-system.md): the §12
    # placement contract — POLICY the on-device solver resolves against the room.
    # Also carries the stream split: anchoring.mode = "object" (tracked) | "world".
    placement = decide_placement(understanding, assets, tracked=tracked,
                                 reference_object_url=reference_object_url,
                                 tracked_object_id=tracked_object_id)

    phase0 = [a["id"] for a in assets if a["status"] != "placeholder"][:3]
    return {
        "schemaVersion": SCHEMA,
        "experienceId": plan.experienceId,
        "title": (plan.subject or "Experience").strip().title(),
        "source": {"kind": kind, "input": (input_text or "")[:600], "subjectHint": subject_hint or ""},
        "understanding": understanding,
        "stage": {"anchor": plan.placement.anchor, "layout": plan.placement.layout,
                  "facing": plan.placement.facing, "scaleMode": plan.placement.scale_mode},
        "placement": placement,
        "anchoring": placement["anchoring"],
        "assets": assets,
        "assetRequirements": asset_requirements,
        "composer": design["composer"],
        # the experience = a SEQUENCE of steps that play baked clips, + triggers
        # (interaction). Motion lives in the asset's clips; SPATAIL plays/sequences.
        "sequence": design["sequence"],
        "triggers": design["triggers"],
        "clips": design["clips"],
        "clipsUsed": design["clipsUsed"],
        "objectives": design["objectives"],
        "progressive": {"firstInteractiveMsBudget": 800, "phase0Assets": phase0,
                        "needsGeneration": [a["id"] for a in assets if a["status"] == "placeholder"]},
    }


def build_from_image(image_bytes: bytes, *, mime: str = "image/jpeg", note: str = "",
                     question: str = "", use_llm: bool = True, library=None,
                     tracked: bool = False, reference_object_url: str | None = None,
                     tracked_object_id: str | None = None) -> dict:
    """Phone photo + question -> Gemini mechanism brief -> the modular experience.

    Adds a `generation` block (the Blender mechanism brief Gemini authored + the
    target primary asset) so the server can build the real animated USDZ on the PC
    and the phone can stream it in over the placeholder.
    """
    import vision
    b = vision.brief_from_image(image_bytes, mime=mime, note=note, question=question)
    c = build_modular_experience(b["prompt"], kind="image", subject_hint=b["subject"],
                                 summary=b["summary"], use_llm=use_llm, library=library,
                                 tracked=tracked, reference_object_url=reference_object_url,
                                 tracked_object_id=tracked_object_id)
    c["source"]["vision"] = b
    primary = next((a["id"] for a in c["assets"] if a.get("role") == "primary_object"),
                   (c["assets"][0]["id"] if c["assets"] else "hero"))
    c["generation"] = {
        "brief": b.get("mechanism_brief", ""),
        "subject": b.get("subject", ""),
        "answer": b.get("answer", ""),
        "question": b.get("question", question or ""),
        "assetId": primary,
    }
    return c
