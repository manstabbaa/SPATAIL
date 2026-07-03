"""settings.py — the SETTING composer (context-first staging).

THE SETTING LAW (canon): placement is computed against context — the real room
when there is one (AR), a brain-composed setting when there isn't (staged).
POST /modular {"context": {"mode": "staged"}} makes the brain compose a small
staged world the experience anchors into; "ar"/absent emits nothing.

Setting block shape (contract-level, additive, optional — normative):

    {"id", "why", "elements": [{"id", "subject", "assetPath", "generationJobId",
      "footprintMeters": [w, h, d], "pose": {"position": [x, y, z], "yawDeg"}}],
     "experienceAnchor": {"elementId", "name", "position", "yawDeg"},
     "ground": {"kind", "sizeMeters": [w, d]},
     "ambiance": {"style", "light"}}

Semantics: metres, world space, ground plane at y=0. assetPath null +
generationJobId set => the asset is being generated on the PC (the client polls
/jobs/{id} and hot-swaps); both null => pending with a reserved footprint only —
the client renders a clearly-labeled materializing spawn-in field at
footprintMeters bounds, NEVER a fake solid object. NEVER a placeholder assetPath.

Two paths, same output shape (the composer.py pattern):
  - deterministic TEMPLATES first — always available, curated per
    domain/keywords, with 2-3 layout variants seeded by the experienceId hash
    ("random but contextual": deterministic per experience, replays are stable);
  - an OPTIONAL Gemini hook (llm.py plumbing) that authors a bespoke setting,
    with the template path as the fallback whenever the LLM is unavailable,
    slow, or malformed.

Element assets resolve through the library (studio/library/asset_library.py):
a real-GLB hit fills assetPath; a miss stays pending (the job server enqueues
generation and fills generationJobId when it can — pending_elements() below is
that seam).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

STYLE = "3d-pastel"

_WORD = re.compile(r"[^a-z0-9]+")


def _tokens(text: str) -> set:
    toks = set(t for t in _WORD.split((text or "").lower()) if t)
    for t in list(toks):
        if len(t) > 3 and t.endswith("s"):
            toks.add(t[:-1])
    return toks


def _slug(text: str) -> str:
    return _WORD.sub("_", (text or "").lower()).strip("_")[:48] or "prop"


def _rot_y(offset, yaw_deg: float):
    """Rotate a local [x, y, z] offset about +Y (glTF convention: +yaw turns
    +x toward -z), so element-relative anchors land right for any variant."""
    r = math.radians(yaw_deg)
    c, s = math.cos(r), math.sin(r)
    x, y, z = offset
    return [x * c + z * s, y, -x * s + z * c]


# ── curated templates ────────────────────────────────────────────────────────
# Each template: why + ground + ambiance + an experienceAnchor spec + 2-3
# layout VARIANTS (the hash of the experienceId picks one — deterministic per
# experience, varied across experiences). Anchor spec is either element-relative
# (elementId + localOffset resolved against that element's variant pose) or
# absolute per variant (elementId None). Footprints/positions in metres, y=0
# ground, poses are ground-contact centers unless the prop sits ON another.
TEMPLATES = {
    "garage": {
        "why": "A garage with the hood up is the natural place to look inside an engine.",
        "ground": {"kind": "floor", "sizeMeters": [8, 8]},
        "ambiance": {"style": STYLE, "light": "soft-day"},
        "anchor": {"elementId": "car", "name": "engine_bay",
                   "localOffset": [1.75, 0.9, 0.0]},   # nose of a 4.6 m sedan, bay height
        "variants": [
            {"elements": [
                {"id": "car", "subject": "sedan car with its hood open",
                 "footprintMeters": [4.6, 1.5, 1.8],
                 "pose": {"position": [0.0, 0.0, -1.6], "yawDeg": -90}},
                {"id": "tool_cart", "subject": "rolling tool cart with drawers",
                 "footprintMeters": [0.75, 1.0, 0.5],
                 "pose": {"position": [1.25, 0.0, -0.2], "yawDeg": -120}},
            ]},
            {"elements": [
                {"id": "car", "subject": "sedan car with its hood open",
                 "footprintMeters": [4.6, 1.5, 1.8],
                 "pose": {"position": [-0.4, 0.0, -2.0], "yawDeg": -55}},
                {"id": "tool_cart", "subject": "rolling tool cart with drawers",
                 "footprintMeters": [0.75, 1.0, 0.5],
                 "pose": {"position": [1.6, 0.0, -1.5], "yawDeg": -30}},
            ]},
            {"elements": [
                {"id": "car", "subject": "sedan car with its hood open",
                 "footprintMeters": [4.6, 1.5, 1.8],
                 "pose": {"position": [0.4, 0.0, -2.0], "yawDeg": -125}},
                {"id": "tool_cart", "subject": "rolling tool cart with drawers",
                 "footprintMeters": [0.75, 1.0, 0.5],
                 "pose": {"position": [-1.6, 0.0, -1.4], "yawDeg": 40}},
            ]},
        ],
    },
    "night_deck": {
        "why": "An open night deck under the stars stages the sky itself as the subject.",
        "ground": {"kind": "floor", "sizeMeters": [10, 10]},
        "ambiance": {"style": STYLE, "light": "starlit-night"},
        "anchor": {"elementId": None, "name": "sky_view"},
        "variants": [
            {"anchor": {"position": [0.0, 1.5, -2.2], "yawDeg": 0},
             "elements": [
                {"id": "telescope", "subject": "astronomer telescope on a tripod",
                 "footprintMeters": [0.7, 1.5, 0.7],
                 "pose": {"position": [0.9, 0.0, -1.3], "yawDeg": -30}},
                {"id": "deck_bench", "subject": "wooden deck bench",
                 "footprintMeters": [1.4, 0.45, 0.55],
                 "pose": {"position": [-1.2, 0.0, -0.9], "yawDeg": 20}},
            ]},
            {"anchor": {"position": [0.2, 1.6, -2.4], "yawDeg": 0},
             "elements": [
                {"id": "telescope", "subject": "astronomer telescope on a tripod",
                 "footprintMeters": [0.7, 1.5, 0.7],
                 "pose": {"position": [-0.9, 0.0, -1.4], "yawDeg": 35}},
                {"id": "deck_bench", "subject": "wooden deck bench",
                 "footprintMeters": [1.4, 0.45, 0.55],
                 "pose": {"position": [1.2, 0.0, -0.8], "yawDeg": -15}},
            ]},
        ],
    },
    "lab_bench": {
        "why": "A lab bench puts living things where science examines them up close.",
        "ground": {"kind": "floor", "sizeMeters": [8, 8]},
        "ambiance": {"style": STYLE, "light": "bright-clinical"},
        "anchor": {"elementId": "bench", "name": "bench_top",
                   "localOffset": [0.0, 1.0, 0.0]},
        "variants": [
            {"elements": [
                {"id": "bench", "subject": "laboratory workbench",
                 "footprintMeters": [1.8, 0.95, 0.8],
                 "pose": {"position": [0.0, 0.0, -1.1], "yawDeg": 0}},
                {"id": "microscope", "subject": "laboratory microscope",
                 "footprintMeters": [0.25, 0.4, 0.3],
                 "pose": {"position": [-0.6, 0.95, -1.1], "yawDeg": 15}},
                {"id": "stool", "subject": "lab stool",
                 "footprintMeters": [0.4, 0.65, 0.4],
                 "pose": {"position": [0.8, 0.0, -0.7], "yawDeg": 0}},
            ]},
            {"elements": [
                {"id": "bench", "subject": "laboratory workbench",
                 "footprintMeters": [1.8, 0.95, 0.8],
                 "pose": {"position": [0.2, 0.0, -1.2], "yawDeg": -20}},
                {"id": "microscope", "subject": "laboratory microscope",
                 "footprintMeters": [0.25, 0.4, 0.3],
                 "pose": {"position": [-0.41, 0.95, -1.42], "yawDeg": -20}},
                {"id": "stool", "subject": "lab stool",
                 "footprintMeters": [0.4, 0.65, 0.4],
                 "pose": {"position": [-0.9, 0.0, -0.8], "yawDeg": 25}},
            ]},
        ],
    },
    "showroom": {
        "why": "A showroom floor presents the product the way a buyer would meet it.",
        "ground": {"kind": "floor", "sizeMeters": [8, 8]},
        "ambiance": {"style": STYLE, "light": "gallery-soft"},
        "anchor": {"elementId": "platform", "name": "display_platform",
                   "localOffset": [0.0, 0.15, 0.0]},
        "variants": [
            {"elements": [
                {"id": "platform", "subject": "round display platform",
                 "footprintMeters": [2.2, 0.12, 2.2],
                 "pose": {"position": [0.0, 0.0, -1.8], "yawDeg": 0}},
                {"id": "plant", "subject": "potted plant",
                 "footprintMeters": [0.5, 1.3, 0.5],
                 "pose": {"position": [1.7, 0.0, -2.6], "yawDeg": 0}},
            ]},
            {"elements": [
                {"id": "platform", "subject": "round display platform",
                 "footprintMeters": [2.2, 0.12, 2.2],
                 "pose": {"position": [-0.3, 0.0, -2.0], "yawDeg": 30}},
                {"id": "plant", "subject": "potted plant",
                 "footprintMeters": [0.5, 1.3, 0.5],
                 "pose": {"position": [1.5, 0.0, -1.2], "yawDeg": 0}},
            ]},
        ],
    },
    "studio": {
        "why": "A clean studio floor keeps every eye on the subject itself.",
        "ground": {"kind": "floor", "sizeMeters": [6, 6]},
        "ambiance": {"style": STYLE, "light": "soft-day"},
        "anchor": {"elementId": None, "name": "stage_center"},
        "variants": [
            {"anchor": {"position": [0.0, 0.0, -1.2], "yawDeg": 0}, "elements": []},
            {"anchor": {"position": [0.0, 0.0, -1.5], "yawDeg": 0}, "elements": []},
        ],
    },
}

DEFAULT_TEMPLATE = "studio"

# template -> (engine domains that pick it, keywords that pick it)
_SELECTORS = {
    "garage": ({"mechanical", "manufacturing"},
               {"engine", "motor", "piston", "cylinder", "v8", "v6", "v12",
                "crankshaft", "camshaft", "turbo", "turbocharger", "transmission",
                "gearbox", "clutch", "carburetor", "combustion", "car", "vehicle",
                "brake", "alternator", "radiator", "exhaust", "ignition"}),
    "night_deck": (set(),
                   {"planet", "star", "moon", "lunar", "solar", "galaxy", "orbit",
                    "comet", "asteroid", "telescope", "constellation", "astronomy",
                    "eclipse", "universe", "cosmos", "nebula", "sun", "mercury",
                    "venus", "mars", "jupiter", "saturn", "uranus", "neptune", "pluto"}),
    "lab_bench": ({"biological"},
                  {"cell", "dna", "rna", "bacteria", "virus", "microbe", "organ",
                   "heart", "lung", "brain", "neuron", "plant", "photosynthesis",
                   "frog", "anatomy", "biology", "enzyme", "protein", "blood",
                   "muscle", "skeleton", "organism", "mitochondria"}),
    "showroom": ({"product"},
                 {"chair", "sofa", "couch", "desk", "lamp", "tv", "television",
                  "furniture", "product", "shelf", "bookshelf", "wardrobe",
                  "speaker", "appliance", "showroom", "headphones", "laptop",
                  "phone", "watch", "shoe", "sneaker", "bag"}),
}


def select_template(understanding: dict) -> str:
    """Pick the curated template for this question: keyword hits (specific
    signal) + a domain boost; nothing matched -> the minimal default."""
    u = understanding or {}
    toks = _tokens(" ".join(str(u.get(k) or "")
                            for k in ("subject", "summary", "intent", "reasoning")))
    domain = str(u.get("domain") or "").strip().lower()
    best, best_score = DEFAULT_TEMPLATE, 0.0
    for key, (domains, kws) in _SELECTORS.items():
        score = float(len(toks & kws))
        if domain and domain in domains:
            score += 2.0
        if score > best_score:
            best, best_score = key, score
    return best if best_score > 0 else DEFAULT_TEMPLATE


def _hash8(experience_id: str) -> str:
    return hashlib.sha256((experience_id or "").encode("utf-8")).hexdigest()[:8]


def _resolve_anchor(template: dict, variant: dict, elements: list) -> dict:
    spec = dict(template.get("anchor") or {})
    spec.update(variant.get("anchor") or {})
    el_id = spec.get("elementId")
    if el_id:
        el = next((e for e in elements if e["id"] == el_id), None)
        if el is not None:
            pos, yaw = el["pose"]["position"], float(el["pose"]["yawDeg"])
            off = _rot_y(spec.get("localOffset", [0.0, 0.0, 0.0]), yaw)
            return {"elementId": el_id, "name": spec.get("name", "anchor"),
                    "position": [round(pos[i] + off[i], 3) for i in range(3)],
                    "yawDeg": round((yaw + float(spec.get("localYawDeg", 0.0))) % 360.0, 1)}
    return {"elementId": None, "name": spec.get("name", "anchor"),
            "position": [float(x) for x in spec.get("position", [0.0, 0.0, -1.2])],
            "yawDeg": float(spec.get("yawDeg", 0.0))}


def _template_setting(understanding: dict, experience_id: str) -> dict:
    """The deterministic path — always available. Variant seeded by the
    experienceId hash: varied across experiences, stable per replay (NO
    wall-clock randomness, ever)."""
    key = select_template(understanding)
    t = TEMPLATES[key]
    h = _hash8(experience_id)
    variant = t["variants"][int(h, 16) % len(t["variants"])]
    elements = []
    for e in copy.deepcopy(variant.get("elements") or []):
        elements.append({
            "id": e["id"], "subject": e["subject"],
            "assetPath": None, "generationJobId": None,
            "footprintMeters": [float(x) for x in e["footprintMeters"]],
            "pose": {"position": [float(x) for x in e["pose"]["position"]],
                     "yawDeg": float(e["pose"]["yawDeg"])},
        })
    return {
        "id": "{0}-{1}".format(key, h),
        "why": t["why"],
        "elements": elements,
        "experienceAnchor": _resolve_anchor(t, variant, elements),
        "ground": copy.deepcopy(t["ground"]),
        "ambiance": copy.deepcopy(t["ambiance"]),
    }


# ── optional LLM path (llm.py plumbing; template is the fallback) ────────────
_LLM_PROMPT = """You are the SPATAIL set designer. Compose a small staged SETTING —
a 3d-pastel diorama world an AR lesson is placed into — for this question.

domain: {domain}
subject: {subject}
summary: {summary}

Rules: metres, world space, ground plane at y=0, the viewer stands at the origin
looking toward -z. 0-4 setting elements, each ONE concrete generatable prop
(e.g. "sedan car with its hood open"), everything within 4 m of the origin.
The experienceAnchor is WHERE the lesson content itself anchors (on/next to the
most fitting element, at a comfortable height).

Return STRICT JSON ONLY:
{{"why": "one sentence: why this setting fits the question",
  "elements": [{{"id": "snake_case", "subject": "concrete prop description",
                 "footprintMeters": [w, h, d],
                 "pose": {{"position": [x, y, z], "yawDeg": 0}}}}],
  "experienceAnchor": {{"elementId": "<element id or null>", "name": "snake_case",
                        "position": [x, y, z], "yawDeg": 0}},
  "ground": {{"kind": "floor", "sizeMeters": [w, d]}},
  "ambiance": {{"style": "3d-pastel", "light": "soft-day"}}}}"""


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _clean_vec(raw, n, lo, hi):
    """[n floats] clamped to [lo, hi], or None when malformed."""
    if not isinstance(raw, (list, tuple)) or len(raw) != n:
        return None
    out = []
    for x in raw:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return None
        out.append(round(_clamp(float(x), lo, hi), 3))
    return out


def _clean_llm_setting(raw, experience_id: str):
    """Clamp a Gemini-authored setting into the exact contract shape; None on
    anything malformed (the caller falls back to the template path)."""
    if not isinstance(raw, dict):
        return None
    elements, seen = [], set()
    raw_elements = raw.get("elements")
    if not isinstance(raw_elements, list):
        return None
    for e in raw_elements[:4]:
        if not isinstance(e, dict):
            return None
        subject = str(e.get("subject") or "").strip()
        if not subject:
            return None
        eid = _slug(str(e.get("id") or subject))
        if eid in seen:
            eid = "{0}_{1}".format(eid[:44], len(seen))
        seen.add(eid)
        fp = _clean_vec(e.get("footprintMeters"), 3, 0.05, 12.0)
        pose = e.get("pose") if isinstance(e.get("pose"), dict) else {}
        pos = _clean_vec(pose.get("position"), 3, -6.0, 6.0)
        if fp is None or pos is None:
            return None
        pos[1] = _clamp(pos[1], 0.0, 4.0)
        try:
            yaw = round(float(pose.get("yawDeg", 0.0)) % 360.0, 1)
        except (TypeError, ValueError):
            return None
        elements.append({"id": eid, "subject": subject[:120],
                         "assetPath": None, "generationJobId": None,
                         "footprintMeters": fp,
                         "pose": {"position": pos, "yawDeg": yaw}})
    anc = raw.get("experienceAnchor") if isinstance(raw.get("experienceAnchor"), dict) else {}
    a_pos = _clean_vec(anc.get("position"), 3, -6.0, 6.0) or [0.0, 0.0, -1.2]
    a_pos[1] = _clamp(a_pos[1], 0.0, 4.0)
    el_id = anc.get("elementId")
    if el_id is not None:
        el_id = _slug(str(el_id))
        if el_id not in seen:
            el_id = None
    try:
        a_yaw = round(float(anc.get("yawDeg", 0.0)) % 360.0, 1)
    except (TypeError, ValueError):
        a_yaw = 0.0
    ground = raw.get("ground") if isinstance(raw.get("ground"), dict) else {}
    size = _clean_vec(ground.get("sizeMeters"), 2, 2.0, 16.0) or [8.0, 8.0]
    amb = raw.get("ambiance") if isinstance(raw.get("ambiance"), dict) else {}
    return {
        "id": "bespoke-{0}".format(_hash8(experience_id)),
        "why": str(raw.get("why") or "").strip()[:200] or "A bespoke setting for this question.",
        "elements": elements,
        "experienceAnchor": {"elementId": el_id,
                             "name": _slug(str(anc.get("name") or "anchor"))[:48],
                             "position": a_pos, "yawDeg": a_yaw},
        "ground": {"kind": str(ground.get("kind") or "floor")[:32] or "floor",
                   "sizeMeters": size},
        "ambiance": {"style": str(amb.get("style") or STYLE)[:32] or STYLE,
                     "light": str(amb.get("light") or "soft-day")[:32] or "soft-day"},
    }


def _llm_setting(understanding: dict, experience_id: str):
    """Bespoke Gemini-authored setting via the director's LLM plumbing.
    Raises/returns None on any failure — compose_setting falls back."""
    import llm
    u = understanding or {}
    prompt = _LLM_PROMPT.format(domain=u.get("domain", ""), subject=u.get("subject", ""),
                                summary=u.get("summary", ""))
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "temperature": 0.8}}
    req = urllib.request.Request(llm.API.format(model=llm.MODEL, key=llm._key()),
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    # Tight cap: /modular is SYNCHRONOUS — a slow LLM must fail fast into the
    # deterministic template, not stall the contract.
    r = urllib.request.urlopen(req, timeout=20)
    data = json.load(r)
    txt = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    return _clean_llm_setting(json.loads(txt), experience_id)


# ── element asset resolution (library first; NEVER a placeholder path) ───────
def _resolve_element_assets(setting: dict, library, domain) -> None:
    """Fill assetPath for elements the library already has as REAL GLBs
    (source == 'library'). A 'primitive' tier match is NOT a real model — the
    element stays pending (assetPath null) rather than lying with a stand-in."""
    if library is None:
        return
    for el in setting.get("elements") or []:
        try:
            res = library.resolve(asset_id=_slug(el["subject"]), subject=el["subject"],
                                  domain=domain, semantic_role="environment")
            if getattr(res, "source", "") == "library" and getattr(res, "glbPath", ""):
                el["assetPath"] = res.glbPath
        except Exception:  # noqa: BLE001 — resolution trouble must never kill the setting
            continue


def pending_elements(setting: dict) -> list:
    """Elements still awaiting a real model (assetPath null, no job yet) — the
    job server enqueues generation for these and fills generationJobId; a host
    with no generation path leaves them pending (reserved footprint only)."""
    return [e for e in (setting or {}).get("elements") or []
            if isinstance(e, dict)
            and not e.get("assetPath") and not e.get("generationJobId")]


def element_asset_id(element: dict) -> str:
    """Stable library/job asset id for a setting element (slug of the subject),
    so generated props REGISTER into the library and the next setting resolves
    them directly — the setting library compounds like the asset library."""
    return _slug(str((element or {}).get("subject") or (element or {}).get("id") or ""))


# ── the composer ─────────────────────────────────────────────────────────────
def compose_setting(understanding: dict, experience_id: str, *, library=None,
                    use_llm: bool = False) -> dict:
    """Compose the staged setting for an experience. Deterministic template
    path first-class and always available; the optional Gemini hook may author
    a bespoke setting with the template as fallback. Library hits fill
    assetPath; misses stay pending for the host to enqueue generation."""
    setting = None
    if use_llm:
        try:
            setting = _llm_setting(understanding, experience_id)
        except Exception as e:  # noqa: BLE001
            print("[settings] LLM setting unavailable, using template: {0!r}".format(e)[:200])
            setting = None
    if setting is None:
        setting = _template_setting(understanding, experience_id)
    _resolve_element_assets(setting, library, (understanding or {}).get("domain"))
    return setting


if __name__ == "__main__":
    print(json.dumps(compose_setting(
        {"domain": "mechanical", "subject": "v8 engine",
         "summary": "how does a V8 engine work"}, "exp_demo"), indent=2))
