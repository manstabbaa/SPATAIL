"""vision_report.py — the bpy-free contract for the vision-guided asset pipeline.

This module is imported by BOTH the system-Python driver/Gemini reviewer AND the
in-Blender evidence/apply passes, so it is pure stdlib (no bpy, no numpy).

It owns three things, so the contract lives in ONE place:

  1. the NUMERIC evidence report shape  (asset_report.json)  — built by evidence.py
  2. the VISUAL review shape            (visual_review_result.json) + validation
  3. the reviewer PROMPT                (visual_review_prompt.md)  — the exact
     question Claude (skill/subagent) or Gemini (headless) answers from the pack

plus the orientation MATH (pure python, unit-testable): given the up/front axis the
reviewer picked from the LABELED evidence renders, the rotation that brings the asset
into SPATAIL's canonical Blender pose (up = +Z, front = -Y) — the same pose the
library/meshy exporters already assume (export_yup, USDZ forward=-Z up=Y).

THE RULE (the user's hard rule): evidence.py and the numeric report make NO semantic
decision. They report pixels + numbers. The reviewer decides up/front/scale/class.
apply.py then executes that decision. Nothing here guesses orientation from geometry.
"""
from __future__ import annotations

import math

EVIDENCE_SCHEMA = "spatail-asset-evidence/1"
REVIEW_SCHEMA = "spatail-asset-vision/1"

# Canonical SPATAIL Blender working pose (matches studio/library/bake_assets +
# studio/meshy/anchors: cameras put "front" at -Y; export_yup turns +Z into GLB +Y).
CANONICAL_UP = "+Z"
CANONICAL_FRONT = "-Y"

# The six signed axes the reviewer may name (referencing the labeled contact sheet).
AXES = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")
_AXIS_VEC = {
    "+X": (1.0, 0.0, 0.0), "-X": (-1.0, 0.0, 0.0),
    "+Y": (0.0, 1.0, 0.0), "-Y": (0.0, -1.0, 0.0),
    "+Z": (0.0, 0.0, 1.0), "-Z": (0.0, 0.0, -1.0),
}

# Placement classes the reviewer may assign — these gate how apply.py grounds the
# asset and how the runtime offers it (table-scale vs floor-scale vs wall, etc.).
PLACEMENT_CLASSES = (
    "table",            # rests on a tabletop (mug, frog, gear, small model)
    "floor",            # stands on the floor (chair, fridge, person, plant)
    "wall",             # mounts on a vertical surface (poster, clock, panel)
    "ceiling",          # hangs from above (lamp, fan)
    "vehicle",          # ground vehicle aligned to a driving plane (car, truck)
    "handheld",         # held / hand-prop scale (phone, tool, controller)
    "educational_model",# a schematic/figurine the LEARNER scales freely (atom, cell)
    "unknown",
)

MATERIAL_QUALITY = ("good", "fair", "poor", "unknown")

# Repair ops apply.py knows how to run (conservative, each logged). The reviewer may
# request a SUBSET; an unknown op is dropped on validation.
REPAIR_OPS = ("recalc_normals_outside", "remove_loose", "merge_by_distance",
              "fill_holes", "flip_normals")


# ── numeric evidence report (built inside Blender by evidence.py) ────────────────

def build_evidence_report(*, asset_id, subject, source, frame, bbox, geometry,
                          health, views, contact_sheet, timings,
                          warnings=None) -> dict:
    """Assemble asset_report.json from MEASURED facts. No interpretation.

    frame    : how evidence.py framed the asset (center + uniform scale for renders)
    bbox     : source-space AABB in metres (size/center/min/max/largest_dim)
    geometry : tri/vert/mesh/material/texture counts + uv/flags
    health   : loose / non-manifold / normals_valid / thin-axis ratios / symmetry cues
    views    : {name: relative_png_path}
    """
    return {
        "schema": EVIDENCE_SCHEMA,
        "asset_id": asset_id,
        "subject": subject,
        "source_glb": source,
        "evidence_frame": frame,
        "source_bbox_m": bbox,
        "geometry": geometry,
        "health": health,
        "views": views,
        "contact_sheet": contact_sheet,
        "timings_seconds": {k: round(float(v), 4) for k, v in (timings or {}).items()},
        "warnings": list(warnings or []),
    }


# ── visual review shape + validation ─────────────────────────────────────────────

def empty_review(reason: str = "") -> dict:
    """A neutral review that makes apply.py a no-op reorientation (identity) and
    falls back to object_size for scale — used when no reviewer ran."""
    return {
        "schema": REVIEW_SCHEMA,
        "reviewer": "none",
        "identity": {"label": "", "confidence": 0.0, "notes": reason},
        "orientation": {"up_axis": CANONICAL_UP, "front_axis": CANONICAL_FRONT,
                        "needs_rotation": False, "confidence": 0.0, "notes": reason},
        "scale": {"believable": False, "estimated_longest_dim_m": 0.0,
                  "placement_class": "unknown", "confidence": 0.0, "notes": reason},
        "quality": {"material_quality": "unknown", "needs_repair": False,
                    "repair": [], "xr_ready": True, "issues": []},
    }


def validate_review(raw: dict, *, reviewer: str = "unknown") -> dict:
    """Coerce a reviewer's JSON into a safe, complete review. Never raises.

    Bad/missing fields fall back to neutral defaults; axes are checked against AXES;
    a degenerate orientation (up == front, or parallel) is repaired to canonical."""
    raw = raw if isinstance(raw, dict) else {}
    out = empty_review()
    out["reviewer"] = reviewer
    in_schema = str(raw.get("schema") or "").strip()
    schema_note = (f"input schema {in_schema!r} != {REVIEW_SCHEMA}"
                   if in_schema and in_schema != REVIEW_SCHEMA else "")

    idn = raw.get("identity") or {}
    out["identity"] = {
        "label": str(idn.get("label") or "")[:80],
        "confidence": _clamp01(idn.get("confidence", 0.5)),
        "notes": str(idn.get("notes") or "")[:280],
    }

    o = raw.get("orientation") or {}
    up = _axis(o.get("up_axis"), CANONICAL_UP)
    front = _axis(o.get("front_axis"), CANONICAL_FRONT)
    if _parallel(up, front):                       # degenerate → pick a front ⟂ up
        front = next(a for a in ("-Y", "+X", "+Z", "-Z", "+Y", "-X")
                     if not _parallel(up, a))
    needs = bool(o.get("needs_rotation", up != CANONICAL_UP or front != CANONICAL_FRONT))
    out["orientation"] = {
        "up_axis": up, "front_axis": front, "needs_rotation": needs,
        "confidence": _clamp01(o.get("confidence", 0.5)),
        "notes": str(o.get("notes") or "")[:280],
    }

    s = raw.get("scale") or {}
    cls = str(s.get("placement_class") or "unknown").strip().lower()
    if cls not in PLACEMENT_CLASSES:
        cls = "unknown"
    out["scale"] = {
        "believable": bool(s.get("believable", False)),
        "estimated_longest_dim_m": _clamp_dim(s.get("estimated_longest_dim_m", 0.0)),
        "placement_class": cls,
        "confidence": _clamp01(s.get("confidence", 0.5)),
        "notes": str(s.get("notes") or "")[:280],
    }

    q = raw.get("quality") or {}
    mq = str(q.get("material_quality") or "unknown").strip().lower()
    if mq not in MATERIAL_QUALITY:
        mq = "unknown"
    repair = [r for r in (q.get("repair") or []) if r in REPAIR_OPS]
    issues = [str(x)[:160] for x in (q.get("issues") or [])][:8]
    if schema_note:
        issues.append(schema_note)
    out["quality"] = {
        "material_quality": mq,
        "needs_repair": bool(q.get("needs_repair", bool(repair))),
        "repair": repair,
        "xr_ready": bool(q.get("xr_ready", True)),
        "issues": issues,
    }
    return out


def _axis(v, default: str) -> str:
    s = str(v or "").strip().upper().replace(" ", "")
    if s in AXES:
        return s
    # tolerate "Z+", "z", "+z", "up"/"front" prose is NOT accepted (must be an axis)
    if len(s) == 2 and s[0] in "XYZ" and s[1] in "+-":
        s = s[1] + s[0]
        if s in AXES:
            return s
    if s in ("X", "Y", "Z"):
        return "+" + s
    return default


def _clamp01(v) -> float:
    try:
        return round(min(max(float(v), 0.0), 1.0), 3)
    except (TypeError, ValueError):
        return 0.5


def _clamp_dim(v) -> float:
    try:
        return round(min(max(float(v), 0.0), 30.0), 4)   # 0 = "no estimate"
    except (TypeError, ValueError):
        return 0.0


# ── orientation math (pure python — verifiable without Blender) ──────────────────

def _norm(v):
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _parallel(axis_a: str, axis_b: str) -> bool:
    a, b = _AXIS_VEC[axis_a], _AXIS_VEC[axis_b]
    return abs(_dot(a, b)) > 0.999


def axis_vec(name: str):
    return _AXIS_VEC[name]


def rotation_to_canonical(up_axis: str, front_axis: str):
    """Return the 3x3 rotation R (row-major list) that maps the source pose onto the
    canonical pose: R @ up_src = +Z, R @ front_src = -Y.

    front_src is Gram-Schmidt-orthogonalized against up_src first, so a reviewer who
    names a slightly-off front still yields a valid rotation. Pure python so it can be
    sanity-checked outside Blender; apply.py wraps the result in mathutils.Matrix."""
    u_s = _norm(_AXIS_VEC.get(up_axis, _AXIS_VEC[CANONICAL_UP]))
    f_raw = _AXIS_VEC.get(front_axis, _AXIS_VEC[CANONICAL_FRONT])
    # orthogonalize front against up
    d = _dot(f_raw, u_s)
    fx = (f_raw[0] - d * u_s[0], f_raw[1] - d * u_s[1], f_raw[2] - d * u_s[2])
    if (fx[0] * fx[0] + fx[1] * fx[1] + fx[2] * fx[2]) < 1e-9:   # front ∥ up → any ⟂ axis
        fx = next(c for c in ((0, -1, 0), (1, 0, 0), (0, 0, 1)) if abs(_dot(c, u_s)) < 0.999)
    f_s = _norm(fx)
    r_s = _cross(f_s, u_s)                              # right-ish third axis

    u_t = _AXIS_VEC[CANONICAL_UP]                       # +Z
    f_t = _AXIS_VEC[CANONICAL_FRONT]                    # -Y
    r_t = _cross(f_t, u_t)

    # Source basis S has columns [f_s, u_s, r_s]; target T has columns [f_t, u_t, r_t].
    # R = T @ S^T maps f_s->f_t, u_s->u_t, r_s->r_t (S orthonormal => S^-1 = S^T).
    S = [[f_s[i], u_s[i], r_s[i]] for i in range(3)]    # 3x3, S[row][col]
    T = [[f_t[i], u_t[i], r_t[i]] for i in range(3)]
    # R[i][j] = sum_k T[i][k] * S[j][k]   (T @ S^T)
    R = [[sum(T[i][k] * S[j][k] for k in range(3)) for j in range(3)] for i in range(3)]
    return R


def reviewer_prompt(asset_id: str, subject: str, report: dict) -> str:
    """The exact question the reviewer answers from the contact sheet + report.
    Written to reports/visual_review_prompt.md and reused by the Gemini reviewer."""
    g = report.get("geometry", {})
    b = report.get("source_bbox_m", {})
    h = report.get("health", {})
    size = b.get("size", [0, 0, 0])
    subj = subject or asset_id.replace("_", " ")
    scanned = h.get("manifold_scanned", True)
    loose_s = h.get("loose_geometry") if scanned else "unknown (mesh too heavy to scan)"
    nm_s = h.get("non_manifold")
    nm_s = "unknown (not scanned)" if (nm_s is None) else nm_s
    return f"""# Visual review — {asset_id} ("{subj}")

You are the SEMANTIC reviewer for a 3D asset that will be placed in AR. Blender has
rendered an evidence pack; YOUR job is to make the decisions Blender must NOT guess.

Look at **contact_sheet.png** (and the individual `preview/*.png` if you need detail).
The iso tiles carry an axis triad (+X red, +Y green, +Z blue/up) and a ground grid at
Z=0. The six ortho tiles are named by the FACE they show: `front`=the -Y side, `back`=+Y,
`right`=+X, `left`=-X, `top`=+Z, `bottom`=-Z (and +Z is image-up in every tile). Reference
those axes for orientation. NOTE: the renders are framed to a uniform size, so judge
ABSOLUTE scale from the object's identity (what a real "{subj}" measures), and
PROPORTION from the renders + the bbox aspect ratios in the panel.

## Measured facts (from Blender — trust these for numbers, not for meaning)
- source bounding box (metres, as imported): {size}
- triangles: {g.get('triangle_count')}, meshes: {g.get('mesh_count')}, materials: {g.get('material_count')}, textured: {g.get('has_textures')}
- normals look valid: {h.get('normals_valid')}, loose geometry: {loose_s}, non-manifold: {nm_s}
- flattest axis ratio (thin/long): {h.get('flatness')}

## Decide (answer ONLY about what you SEE)
1. **identity** — what is this object, really? (label + confidence)
2. **orientation** — which CURRENT axis (referencing the labeled renders) is the
   object's true **up**, and which is its natural **front**? Most AR assets should
   stand up and face the viewer. If the imported pose is already correct, up=+Z,
   front=-Y, needs_rotation=false.
3. **scale** — is the object's REAL-WORLD size believable as a "{subj}"? Give your
   best estimate of its longest real dimension in metres, and a placement_class:
   {", ".join(PLACEMENT_CLASSES)}.
4. **quality** — material_quality (good/fair/poor), any repair needed
   ({", ".join(REPAIR_OPS)}), is it XR-ready, and list visible defects.

## Return STRICT JSON ONLY (schema {REVIEW_SCHEMA})
{{
  "identity": {{"label": "frog", "confidence": 0.9, "notes": ""}},
  "orientation": {{"up_axis": "+Z", "front_axis": "-Y", "needs_rotation": false,
                  "confidence": 0.9, "notes": "why"}},
  "scale": {{"believable": false, "estimated_longest_dim_m": 0.09,
            "placement_class": "table", "confidence": 0.8, "notes": "why"}},
  "quality": {{"material_quality": "good", "needs_repair": false, "repair": [],
              "xr_ready": true, "issues": []}}
}}"""


if __name__ == "__main__":
    import json
    # sanity: a model imported lying on its back (true up = +Y) facing -Z
    R = rotation_to_canonical("+Y", "-Z")
    print("R(up=+Y, front=-Z):")
    for row in R:
        print("  ", [round(x, 3) for x in row])
    # R @ (0,1,0) should be (0,0,1) = +Z;  R @ (0,0,-1) should be (0,-1,0) = -Y
    def mul(R, v):
        return [sum(R[i][k] * v[k] for k in range(3)) for i in range(3)]
    print("  up_src(+Y) ->", [round(x, 3) for x in mul(R, [0, 1, 0])], "(expect 0,0,1)")
    print("  front_src(-Z) ->", [round(x, 3) for x in mul(R, [0, 0, -1])], "(expect 0,-1,0)")
    print(json.dumps(validate_review({"orientation": {"up_axis": "y", "front_axis": "y"}}), indent=2))
