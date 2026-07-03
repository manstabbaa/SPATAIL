"""trace_store.py — placement traces + schema truth (LIVE_BRAIN_SPEC §2).

Persists every /modular decision so it can be attributed later (the /traces/view
instrument): one JSON file per experience at

    studio/out/traces/{experienceId}.json
    -> {"experienceId", "createdAt", "prompt", "contract", "sceneContract"?,
        "schemaValidation": {"ok", "errors": []}, "reports": []}

`reports[]` collects POST /placement-report bodies — what each client actually
did with the contract (solver inputs, plan, final transforms).

Also owns contract validation: a hand-rolled, stdlib-only interpreter of the
JSON-Schema subset used by schemas/modularContract.v0_7.schema.json and
schemas/sceneContract.v0_6.schema.json ($ref/#$defs, type, const, enum,
required, properties, additionalProperties, items, minItems, maxItems,
minLength). WARN-ONLY by design: validate_contract() never raises and never
blocks a response — it stamps {"ok", "errors": [...]} for the trace.

Stdlib only, like job_server. Writes are atomic (tmp + os.replace) so a reader
never sees a torn file.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRACES_DIR = ROOT / "studio" / "out" / "traces"
SCHEMAS_DIR = ROOT / "schemas"

MODULAR_SCHEMA = "modularContract.v0_7.schema.json"
SCENE_SCHEMA = "sceneContract.v0_6.schema.json"

_MAX_ERRORS = 40                     # keep the stamp readable, not exhaustive
_LOCK = threading.Lock()             # serialises read-modify-write on trace files
_ID_RE = re.compile(r"[^A-Za-z0-9._-]")


# ── trace files ──────────────────────────────────────────────────────────────
def iso_now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_id(experience_id: str) -> str:
    """Filename-safe experience id (basename only, no traversal, no dotfiles)."""
    s = _ID_RE.sub("_", str(experience_id or "").strip())[:128].lstrip(".")
    if not s:
        raise ValueError("empty experienceId")
    return s


def trace_path(experience_id: str) -> Path:
    return TRACES_DIR / f"{_safe_id(experience_id)}.json"


def _atomic_write(fp: Path, obj: dict) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_name(f".{fp.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    os.replace(str(tmp), str(fp))


def save(trace: dict) -> Path:
    """Create/replace the trace file for trace['experienceId']. Atomic."""
    fp = trace_path(trace.get("experienceId") or "")
    with _LOCK:
        _atomic_write(fp, trace)
    return fp


def read(experience_id: str):
    """The trace dict, or None when unknown/unreadable."""
    try:
        fp = trace_path(experience_id)
    except ValueError:
        return None
    if not fp.is_file():
        return None
    try:
        t = json.loads(fp.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return t if isinstance(t, dict) else None


def append_report(experience_id: str, report: dict):
    """Append into trace.reports[]; returns the updated trace, None if unknown."""
    with _LOCK:
        trace = read(experience_id)
        if trace is None:
            return None
        reports = trace.get("reports")
        if not isinstance(reports, list):
            reports = []
        reports.append(report)
        trace["reports"] = reports
        _atomic_write(trace_path(experience_id), trace)
        return trace


def list_traces() -> list:
    """[{experienceId, createdAt, title, reportCount}], newest first. The vision
    replan traces live in traces/vision/ subdirs and are deliberately not listed
    here (different lifecycle; glob('*.json') is non-recursive)."""
    out = []
    if not TRACES_DIR.is_dir():
        return out
    for fp in TRACES_DIR.glob("*.json"):
        try:
            t = json.loads(fp.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not isinstance(t, dict):
            continue
        contract = t.get("contract") if isinstance(t.get("contract"), dict) else {}
        out.append({
            "experienceId": t.get("experienceId") or fp.stem,
            "createdAt": t.get("createdAt") or "",
            "title": contract.get("title") or "",
            "reportCount": len(t.get("reports") or []),
        })
    out.sort(key=lambda e: e["createdAt"], reverse=True)
    return out


# ── schema validation (hand-rolled subset; warn-only) ───────────────────────
_TYPES = {"object": dict, "array": list, "string": str,
          "boolean": bool, "null": type(None)}


def _type_ok(value, tname: str) -> bool:
    if tname == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if tname == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    t = _TYPES.get(tname)
    # bool is an int subclass; a boolean must not satisfy plain "integer"/"number",
    # and only "boolean" accepts it.
    if t is None:
        return True                          # unknown type name: don't fail on it
    if t is not bool and isinstance(value, bool):
        return False
    return isinstance(value, t)


def _short(value) -> str:
    s = repr(value)
    return s if len(s) <= 60 else s[:57] + "..."


def _walk(value, schema, path: str, defs: dict, errors: list) -> None:
    if len(errors) >= _MAX_ERRORS or not isinstance(schema, dict):
        return
    ref = schema.get("$ref")
    if isinstance(ref, str):
        if ref.startswith("#/$defs/"):
            _walk(value, defs.get(ref[len("#/$defs/"):], {}), path, defs, errors)
        return
    if "const" in schema:
        if value != schema["const"]:
            errors.append(f"{path}: expected {schema['const']!r}, got {_short(value)}")
        return
    if "enum" in schema:
        if value not in schema["enum"]:
            errors.append(f"{path}: {_short(value)} not in {schema['enum']}")
        return
    t = schema.get("type")
    if t is not None:
        names = t if isinstance(t, list) else [t]
        if not any(_type_ok(value, n) for n in names):
            errors.append(f"{path}: expected {'|'.join(names)}, "
                          f"got {type(value).__name__} {_short(value)}")
            return
    if isinstance(value, str):
        ml = schema.get("minLength")
        if isinstance(ml, int) and len(value) < ml:
            errors.append(f"{path}: shorter than minLength {ml}")
    if isinstance(value, dict):
        for req in schema.get("required") or []:
            if req not in value:
                errors.append(f"{path}: missing required '{req}'")
                if len(errors) >= _MAX_ERRORS:
                    return
        props = schema.get("properties") or {}
        extra = schema.get("additionalProperties")
        for k, v in value.items():
            if k in props:
                _walk(v, props[k], f"{path}.{k}", defs, errors)
            elif isinstance(extra, dict):
                _walk(v, extra, f"{path}.{k}", defs, errors)
    if isinstance(value, list):
        mi, ma = schema.get("minItems"), schema.get("maxItems")
        if isinstance(mi, int) and len(value) < mi:
            errors.append(f"{path}: fewer than minItems {mi}")
        if isinstance(ma, int) and len(value) > ma:
            errors.append(f"{path}: more than maxItems {ma}")
        items = schema.get("items")
        if isinstance(items, dict):
            for i, v in enumerate(value):
                _walk(v, items, f"{path}[{i}]", defs, errors)


def validate(instance, schema: dict) -> list:
    """Errors ([] when valid) for `instance` against a loaded schema dict."""
    errors: list = []
    _walk(instance, schema, "$", schema.get("$defs") or {}, errors)
    return errors[:_MAX_ERRORS]


_SCHEMA_CACHE: dict = {}


def _load_schema(fname: str) -> dict:
    if fname not in _SCHEMA_CACHE:
        _SCHEMA_CACHE[fname] = json.loads(
            (SCHEMAS_DIR / fname).read_text(encoding="utf-8"))
    return _SCHEMA_CACHE[fname]


def validate_contract(contract, scene_contract=None) -> dict:
    """{ok, errors:[str]} for the modular contract (+ its scene projection when
    present). WARN-ONLY: never raises, never blocks — job_server stamps the
    result into the response and the trace and moves on."""
    errors: list = []
    ok = True
    try:
        pairs = [("modular", contract, MODULAR_SCHEMA)]
        if scene_contract is not None:
            pairs.append(("scene", scene_contract, SCENE_SCHEMA))
        for label, inst, fname in pairs:
            try:
                schema = _load_schema(fname)
            except Exception as exc:  # noqa: BLE001
                ok = False
                errors.append(f"{label}: schema unavailable ({exc})")
                continue
            errs = validate(inst, schema)
            if errs:
                ok = False
                errors.extend(f"{label}: {e}" for e in errs)
    except Exception as exc:  # noqa: BLE001
        ok = False
        errors.append(f"validator error: {exc}")
    return {"ok": ok, "errors": errors[:_MAX_ERRORS]}


# ── selftest (pure python; run: python3 studio/server/trace_store.py) ───────
if __name__ == "__main__":
    import sys
    import tempfile

    failures = 0

    def check(name, cond):
        global failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            failures += 1

    # a contract shaped exactly like build_modular_experience() + job_server stamps
    for p in (str(ROOT / "studio"), str(ROOT / "studio" / "director")):
        if p not in sys.path:
            sys.path.insert(0, p)
    from spatail.design_system import decide_placement
    import scene_contract as _sc

    understanding = {"domain": "mechanical", "intent": "explain",
                     "subject": "v8 engine", "summary": "how a v8 works",
                     "reasoning": "exploded view reads best for assemblies"}
    assets = [{
        "id": "engine", "name": "Engine", "role": "primary_object",
        "description": "a v8 engine block", "glbUrl": "", "usdzUrl": "",
        "fallbackPrimitive": "cube", "scaleMeters": [0.9, 0.7, 0.7],
        "supportsAnimation": False, "supportsHighlight": False,
        "supportsTransparency": False, "source": "generate",
        "status": "placeholder", "animation": None,
    }]
    placement = decide_placement(understanding, assets)
    modular = {
        "schemaVersion": "0.7.0-spatail-modular",
        "experienceId": "exp_selftest", "title": "V8 Engine",
        "source": {"kind": "text", "input": "how does a v8 work", "subjectHint": ""},
        "understanding": understanding,
        "stage": {"anchor": "table", "layout": "arc", "facing": "user",
                  "scaleMode": "dynamic"},
        "placement": placement, "anchoring": placement["anchoring"],
        "assets": assets, "assetRequirements": {},
        "composer": "deterministic",
        "sequence": [{"id": "step_0", "title": "Meet the engine", "narration": "…",
                      "focus": "engine", "clip": "demo", "loop": True,
                      "panels": [], "advance": "tap"}],
        "triggers": [{"when": {"event": "onTap", "target": "scene"},
                      "do": [{"action": "advance"}]}],
        "clips": [], "clipsUsed": ["demo"],
        "objectives": [{"id": "understand", "goal": "v8 engine",
                        "summary": "how a v8 works"}],
        "progressive": {"firstInteractiveMsBudget": 800, "phase0Assets": [],
                        "needsGeneration": ["engine"]},
    }
    scene = _sc.to_scene_contract(modular)
    modular["sceneContract"] = scene

    v = validate_contract(modular, scene)
    check(f"emitter-shaped contract validates ({v['errors'][:3]})", v["ok"])

    bad = dict(modular)
    bad.pop("assets")
    bad["composer"] = "vibes"
    v2 = validate_contract(bad)
    check("missing required + bad enum are flagged",
          not v2["ok"] and any("assets" in e for e in v2["errors"])
          and any("composer" in e for e in v2["errors"]))

    v3 = validate_contract({"schemaVersion": "0.7.0-spatail-modular"})
    check("empty contract flags many", not v3["ok"] and len(v3["errors"]) > 5)

    # trace round-trip in a temp dir
    TRACES_DIR = Path(tempfile.mkdtemp())  # noqa: F811  (module global, test only)
    save({"experienceId": "exp_selftest", "createdAt": iso_now(),
          "prompt": "how does a v8 work", "contract": modular,
          "sceneContract": scene, "schemaValidation": v, "reports": []})
    t = read("exp_selftest")
    check("save/read round-trip", t is not None and t["contract"]["title"] == "V8 Engine")
    t2 = append_report("exp_selftest", {"experienceId": "exp_selftest",
                                        "client": "ios", "reportedAt": 0.0})
    check("append_report appends", t2 is not None and len(t2["reports"]) == 1)
    check("append_report unknown -> None", append_report("nope", {}) is None)
    ls = list_traces()
    check("list_traces lists it", len(ls) == 1 and ls[0]["reportCount"] == 1
          and ls[0]["title"] == "V8 Engine")
    check("id sanitised", trace_path("../evil/x").name == "_evil_x.json"
          and os.sep not in trace_path("../evil/x").name)

    if failures:
        print(f"SELFTEST FAIL ({failures})")
        sys.exit(1)
    print("SELFTEST PASS")
