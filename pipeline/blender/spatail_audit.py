"""
spatail_audit.py — pre-classification audit.

Bridges the asset-agnostic treatment stage and the asset-type-specific
classifier stage. Renders 4 orthographic thumbnails of the currently
loaded scene and asks an injected vision callback whether the user's
prompt actually matches what's on screen.

This is the only spatail skill that consumes the user's raw prompt text.
Everything downstream is JSON-only.

USAGE in live Blender (or a Blender batch process):

    exec(open(r"C:/SPATAIL_MAX/pipeline/blender/spatail_audit.py").read())

    def my_vision_cb(image_path, question):
        # Caller wires this to whatever multimodal model is available
        # (Anthropic vision, OpenAI vision, local VLM, etc.). Must return:
        #   {"verdict": "confident_match|likely_match|mismatch|unsure",
        #    "reasoning": "...",
        #    "suggested_reprompt": "..." (optional, mainly for mismatch)}
        ...

    result = audit_asset_vs_prompt(
        treatment_json=r"C:/.../v10_engine.treatment.json",
        prompt_text="what do all the buttons on an F1 steering wheel do?",
        vision_callback=my_vision_cb,
        out_dir=r"C:/.../v10_engine/audit",
    )

If `vision_callback` is None, the skill still renders the thumbnails and
writes an audit JSON with verdict="no_vision_callback". The orchestrator
can then present the image to the user directly.
"""

import bpy
import json
import os
import traceback
from datetime import datetime, timezone
from mathutils import Vector


# ---------------------------------------------------------------------------
# Lazy import of the multiview render helper. We want spatail_audit to work
# whether multiview_render was pre-loaded (via exec) or not.
# ---------------------------------------------------------------------------

def _get_multiview_render():
    fn = globals().get("multiview_render")
    if callable(fn):
        return fn
    # Try to load it ourselves.
    here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() \
           else r"C:/SPATAIL_MAX/pipeline/blender"
    mv_path = os.path.join(here, "spatail_multiview_render.py")
    if os.path.exists(mv_path):
        # Execute in a scoped namespace and pluck the function out.
        ns = {}
        with open(mv_path, "r", encoding="utf-8") as f:
            exec(compile(f.read(), mv_path, "exec"), ns)
        return ns.get("multiview_render")
    return None


# ---------------------------------------------------------------------------
# Treatment manifest parsing
# ---------------------------------------------------------------------------

def _load_treatment(treatment_json):
    with open(treatment_json, "r", encoding="utf-8") as f:
        return json.load(f)


def _asset_facts(manifest):
    """Pull the bits the vision LLM needs to know about asset shape & size.
    Defensive: every field has a fallback so a partially-written manifest
    doesn't crash the audit."""
    summary = manifest.get("summary", {}) or {}
    topo = (manifest.get("stages", {}) or {}).get("2_topology", {}) or {}

    bbox_lo = topo.get("asset_bbox_lo")
    bbox_hi = topo.get("asset_bbox_hi")
    bbox_size = summary.get("asset_bbox_size") or topo.get("asset_bbox_size")

    return {
        "assetId": manifest.get("assetId", "unknown"),
        "bbox_lo": bbox_lo,
        "bbox_hi": bbox_hi,
        "bbox_size": bbox_size,
        "unit_guess": summary.get("unit_guess") or topo.get("unit_guess"),
        "part_count": summary.get("final_part_count"),
        "shape_histogram": summary.get("shape_class_histogram") or {},
    }


def _framing_from_bbox(facts):
    """Return (target_vec, size_scalar) for multiview_render."""
    lo, hi = facts.get("bbox_lo"), facts.get("bbox_hi")
    if lo and hi and len(lo) == 3 and len(hi) == 3:
        centre = Vector(((lo[0] + hi[0]) / 2,
                         (lo[1] + hi[1]) / 2,
                         (lo[2] + hi[2]) / 2))
        size = max(hi[i] - lo[i] for i in range(3)) * 0.55  # half + 10% pad
    elif facts.get("bbox_size"):
        size = max(facts["bbox_size"]) * 0.55
        centre = Vector((0, 0, 0))
    else:
        # Last resort: scan scene.
        meshes = [o for o in bpy.data.objects if o.type == "MESH"]
        if meshes:
            cs = [o.matrix_world.translation for o in meshes]
            centre = sum(cs, Vector()) / len(cs)
            size = 20
        else:
            centre, size = Vector((0, 0, 0)), 20
    if size < 1:
        size = 20
    return centre, float(size)


# ---------------------------------------------------------------------------
# Question construction for the vision callback
# ---------------------------------------------------------------------------

_QUESTION_TEMPLATE = """You are auditing whether a user's prompt matches a 3D asset.

USER PROMPT (verbatim):
\"\"\"{prompt}\"\"\"

ASSET FACTS (from automated mesh treatment, no semantic labels yet):
- assetId: {asset_id}
- part_count: {part_count}
- shape_class_histogram: {shape_hist}
- bbox_size ({units}): {bbox_size}

The attached image is a 2x2 grid showing the asset from four angles:
- Top-left: PERSPECTIVE
- Top-right: FRONT (orthographic, looking down −Y)
- Bottom-left: RIGHT (orthographic, looking down +X)
- Bottom-right: TOP (orthographic, looking down +Z)

QUESTION: Does the user's prompt describe THIS object?

Reply as STRICT JSON with these keys:
  "verdict": one of "confident_match", "likely_match", "mismatch", "unsure"
  "reasoning": one or two sentences explaining the verdict using what you see
  "suggested_reprompt": (only if verdict is "mismatch") a short sentence
      telling the user what the object actually looks like, OR asking them
      to clarify which file they meant to upload.

Rules:
- Be decisive on clear cases. Hammer vs axe? Mismatch.
- "unsure" is reserved for cases where the geometry is genuinely ambiguous
  (e.g. partial CAD, only a few parts visible).
- Do not try to be clever about creative interpretations of the prompt.
- Do NOT rename the asset or invent labels — only judge match/mismatch.
"""


def _build_question(prompt_text, facts):
    return _QUESTION_TEMPLATE.format(
        prompt=prompt_text,
        asset_id=facts["assetId"],
        part_count=facts.get("part_count"),
        shape_hist=json.dumps(facts.get("shape_histogram", {})),
        units=facts.get("unit_guess") or "bu",
        bbox_size=facts.get("bbox_size"),
    )


# ---------------------------------------------------------------------------
# Thumbnail rendering
# ---------------------------------------------------------------------------

_VALID_VERDICTS = {"confident_match", "likely_match", "mismatch", "unsure"}


def _scene_has_mesh():
    return any(o.type == "MESH" for o in bpy.data.objects)


def _render_thumbnails(out_dir, facts):
    """Render the 2x2 grid PNG via spatail_multiview_render. Returns
    (grid_path, {persp,front,right,top: tile_path}). Tiles are kept so a
    vision callback can use them individually if it prefers."""
    os.makedirs(out_dir, exist_ok=True)
    grid_path = os.path.join(out_dir, "thumbnails.png").replace("\\", "/")
    tiles_dir = os.path.join(out_dir, "_tiles").replace("\\", "/")

    target, size = _framing_from_bbox(facts)

    mv = _get_multiview_render()
    if mv is None:
        raise RuntimeError(
            "spatail_multiview_render not available — load it before audit, "
            "or place it next to spatail_audit.py"
        )

    written = mv(
        out_path=grid_path,
        frame=None,
        target=target,
        size=size,
        tile_w=512,
        tile_h=384,
        save_tiles_dir=tiles_dir,
    )

    # Resolve tile paths (multiview_render names them <tag>_<view>.png with
    # tag="_" when frame is None).
    tile_paths = {}
    for nm in ("persp", "front", "right", "top"):
        cand = os.path.join(tiles_dir, f"__{nm}.png").replace("\\", "/")
        if not os.path.exists(cand):
            # Older tagger: "_" prefix
            cand = os.path.join(tiles_dir, f"_{nm}.png").replace("\\", "/")
        tile_paths[nm] = cand if os.path.exists(cand) else None

    # If Pillow was missing, `written` is a .manifest.txt path; the grid
    # PNG won't exist. Reflect that in the result.
    if not str(written).endswith(".png"):
        grid_path = None  # vision callback must use tiles individually
    return grid_path, tile_paths


# ---------------------------------------------------------------------------
# Verdict normalization
# ---------------------------------------------------------------------------

def _normalize_callback_result(raw):
    """The vision callback ideally returns a dict, but be forgiving — accept
    a JSON string too. Validate the verdict against the known set."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {
                "verdict": "unsure",
                "reasoning": f"vision callback returned non-JSON string: {raw[:200]}",
            }
    if not isinstance(raw, dict):
        return {"verdict": "unsure",
                "reasoning": f"vision callback returned unexpected type: {type(raw).__name__}"}

    verdict = raw.get("verdict", "").strip()
    if verdict not in _VALID_VERDICTS:
        return {"verdict": "unsure",
                "reasoning": f"vision callback returned unrecognised verdict '{verdict}'; "
                             f"original reasoning: {raw.get('reasoning', '')}"}
    out = {"verdict": verdict,
           "reasoning": raw.get("reasoning", "")}
    if verdict == "mismatch" and raw.get("suggested_reprompt"):
        out["suggested_reprompt"] = raw["suggested_reprompt"]
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def audit_asset_vs_prompt(treatment_json,
                          prompt_text,
                          vision_callback=None,
                          out_dir=None):
    """Audit whether the user's prompt matches the treated asset.

    Parameters
    ----------
    treatment_json : str
        Path to `<asset>.treatment.json` from spatail-treat-mesh.
    prompt_text : str
        The user's raw prompt text. Passed verbatim into the vision question.
    vision_callback : callable or None
        Function `(image_path, question_text) -> dict`. If None, the skill
        still produces thumbnails and writes audit JSON with
        verdict="no_vision_callback".
    out_dir : str or None
        Where to write thumbnails + audit JSON. Defaults to
        `<treatment_dir>/audit/`.

    Returns
    -------
    dict
        The full audit record. Also written to
        `<out_dir>/<assetId>.audit.json`.
    """
    if not os.path.exists(treatment_json):
        raise FileNotFoundError(f"treatment_json not found: {treatment_json}")
    if not isinstance(prompt_text, str) or not prompt_text.strip():
        raise ValueError("prompt_text must be a non-empty string")

    manifest = _load_treatment(treatment_json)
    facts = _asset_facts(manifest)
    asset_id = facts["assetId"]

    if out_dir is None:
        out_dir = os.path.join(os.path.dirname(treatment_json), "audit")
    out_dir = out_dir.replace("\\", "/")
    os.makedirs(out_dir, exist_ok=True)

    audit = {
        "assetId": asset_id,
        "schemaVersion": "0.1.0-spatail-pre-classification-audit",
        "auditedAt": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "treatment_json": treatment_json.replace("\\", "/"),
            "prompt_text": prompt_text,
        },
        "asset_facts": {
            "bbox_size": facts.get("bbox_size"),
            "unit_guess": facts.get("unit_guess"),
            "part_count": facts.get("part_count"),
            "shape_histogram": facts.get("shape_histogram"),
        },
        "thumbnails_png": None,
        "tiles": {},
        "verdict": None,
        "reasoning": None,
    }

    # --- Scene sanity check ---
    if not _scene_has_mesh():
        audit["verdict"] = "scene_empty"
        audit["reasoning"] = ("No mesh objects in the current Blender scene; "
                              "cannot render thumbnails to audit against.")
        _write_audit(audit, out_dir, asset_id)
        return audit

    # --- Render thumbnails ---
    try:
        grid_path, tile_paths = _render_thumbnails(out_dir, facts)
        audit["thumbnails_png"] = grid_path
        audit["tiles"] = tile_paths
    except Exception as e:
        audit["verdict"] = "render_error"
        audit["reasoning"] = f"thumbnail render failed: {e}\n{traceback.format_exc()}"
        _write_audit(audit, out_dir, asset_id)
        return audit

    # --- Vision callback ---
    if vision_callback is None:
        audit["verdict"] = "no_vision_callback"
        audit["reasoning"] = ("No vision callback supplied. Thumbnails were "
                              "rendered; orchestrator should ask the user "
                              "to inspect them directly.")
        _write_audit(audit, out_dir, asset_id)
        return audit

    question = _build_question(prompt_text, facts)
    audit["question_to_vision_model"] = question

    image_for_callback = audit["thumbnails_png"] or tile_paths
    try:
        raw = vision_callback(image_for_callback, question)
    except Exception as e:
        audit["verdict"] = "callback_error"
        audit["reasoning"] = f"vision_callback raised: {e}\n{traceback.format_exc()}"
        _write_audit(audit, out_dir, asset_id)
        return audit

    normalized = _normalize_callback_result(raw)
    audit.update(normalized)
    _write_audit(audit, out_dir, asset_id)
    return audit


def _write_audit(audit, out_dir, asset_id):
    out_path = os.path.join(out_dir, f"{asset_id}.audit.json").replace("\\", "/")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2)
    audit["_writtenTo"] = out_path
    print(f"[spatail_audit] verdict={audit.get('verdict')} → {out_path}")
    return out_path


print("[spatail_audit] module loaded.")
