"""test_settings.py — stdlib-only tests for the setting composer
(studio/director/settings.py, the setting law: context-first staging).

Run:  python3 studio/director/tests/test_settings.py
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_HERE = Path(__file__).resolve()
ROOT = _HERE.parents[3]                                  # repo root
sys.path.insert(0, str(_HERE.parents[1]))                # studio/director
sys.path.insert(0, str(ROOT / "studio" / "server"))      # trace_store (schema truth)

import settings  # noqa: E402
import trace_store  # noqa: E402

FAILURES = 0


def check(name, cond):
    global FAILURES
    print("  [{0}] {1}".format("PASS" if cond else "FAIL", name))
    if not cond:
        FAILURES += 1


# the normative contract shape — these key sets ARE the spec
SETTING_KEYS = {"id", "why", "elements", "experienceAnchor", "ground", "ambiance"}
ELEMENT_KEYS = {"id", "subject", "assetPath", "generationJobId",
                "footprintMeters", "pose"}
ANCHOR_KEYS = {"elementId", "name", "position", "yawDeg"}
POSE_KEYS = {"position", "yawDeg"}


def shape_ok(s):
    """True when the emitted JSON keys match the spec exactly (no extras,
    no omissions), at every level."""
    if set(s.keys()) != SETTING_KEYS:
        return False
    if set(s["experienceAnchor"].keys()) != ANCHOR_KEYS:
        return False
    if set(s["ground"].keys()) != {"kind", "sizeMeters"}:
        return False
    if set(s["ambiance"].keys()) != {"style", "light"}:
        return False
    for e in s["elements"]:
        if set(e.keys()) != ELEMENT_KEYS or set(e["pose"].keys()) != POSE_KEYS:
            return False
        if len(e["footprintMeters"]) != 3 or len(e["pose"]["position"]) != 3:
            return False
    return len(s["experienceAnchor"]["position"]) == 3 and len(s["ground"]["sizeMeters"]) == 2


V8 = {"domain": "mechanical", "subject": "v8 engine",
      "summary": "how does a V8 engine work"}

print("template selection")
garage = settings.compose_setting(V8, "exp_v8_001")
check("v8 engine -> garage template", garage["id"].startswith("garage-"))
check("experienceAnchor is engine_bay",
      garage["experienceAnchor"]["name"] == "engine_bay")
check("engine_bay anchors ON the car element",
      garage["experienceAnchor"]["elementId"] == "car")
check("engine_bay sits at the bay height (~0.9 m)",
      0.5 <= garage["experienceAnchor"]["position"][1] <= 1.3)
car = next((e for e in garage["elements"] if e["id"] == "car"), None)
check("car element present with 'hood' in subject",
      car is not None and "hood" in car["subject"])
check("car footprint is sedan-sized (~4.6 m long)",
      car is not None and car["footprintMeters"][0] > 4.0)
check("garage carries a why sentence", bool(garage["why"].strip()))

# keyword-only selection (no domain hint) still lands the garage
kw_only = settings.compose_setting({"summary": "how does a V8 engine work"}, "exp_kw")
check("keyword-only prompt also -> garage", kw_only["id"].startswith("garage-"))

# the other curated templates
deck = settings.compose_setting(
    {"domain": "scientific", "subject": "saturn", "summary": "why does saturn have rings"},
    "exp_astro")
check("astronomy -> night deck", deck["id"].startswith("night_deck-"))
lab = settings.compose_setting(
    {"domain": "biological", "subject": "frog heart", "summary": "how does a frog's heart pump blood"},
    "exp_bio")
check("biology -> lab bench", lab["id"].startswith("lab_bench-"))
shop = settings.compose_setting(
    {"domain": "product", "subject": "sofa", "summary": "show me this sofa"},
    "exp_prod")
check("product/furniture -> showroom", shop["id"].startswith("showroom-"))

print("default fallback")
misc = settings.compose_setting(
    {"domain": "general_knowledge", "subject": "qwzzyx", "summary": "tell me about qwzzyx blorp"},
    "exp_misc")
check("unmatched prompt -> studio floor", misc["id"].startswith("studio-"))
check("studio floor is minimal (ground + light, no props)",
      misc["elements"] == [] and misc["ground"]["kind"] == "floor"
      and bool(misc["ambiance"]["light"]))

print("emitted JSON shape matches the spec exactly")
for name, s in (("garage", garage), ("night_deck", deck), ("lab_bench", lab),
                ("showroom", shop), ("studio", misc)):
    check("{0} setting keys match spec".format(name), shape_ok(s))
check("setting is JSON-serializable",
      json.loads(json.dumps(garage)) == garage)
check("pending elements carry null assetPath AND null generationJobId",
      all(e["assetPath"] is None and e["generationJobId"] is None
          for e in garage["elements"]))

print("schema truth (trace_store validator against the new $defs)")
schema = json.loads(
    (ROOT / "schemas" / "modularContract.v0_7.schema.json").read_text(encoding="utf-8"))
sub = {"$ref": "#/$defs/setting", "$defs": schema["$defs"]}
for name, s in (("garage", garage), ("studio", misc), ("lab_bench", lab)):
    errs = trace_store.validate(s, sub)
    check("{0} validates against modularContract $defs.setting ({1})".format(
        name, errs[:2]), not errs)
scene_schema = json.loads(
    (ROOT / "schemas" / "sceneContract.v0_6.schema.json").read_text(encoding="utf-8"))
errs = trace_store.validate(
    garage, {"$ref": "#/$defs/setting", "$defs": scene_schema["$defs"]})
check("garage validates against sceneContract $defs.setting too", not errs)

print("variation: deterministic per experienceId, varied across experiences")
again = settings.compose_setting(V8, "exp_v8_001")
check("same experienceId -> byte-identical setting",
      json.dumps(garage, sort_keys=True) == json.dumps(again, sort_keys=True))
layouts = set()
stable = True
for i in range(12):
    eid = "exp_var_{0}".format(i)
    a = settings.compose_setting(V8, eid)
    b = settings.compose_setting(V8, eid)
    stable = stable and (json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True))
    layouts.add(json.dumps(a["elements"], sort_keys=True))
check("every replay is stable across 12 ids", stable)
check("multiple layout variants appear across experienceIds", len(layouts) >= 2)
check("variant count within template bounds",
      len(layouts) <= len(settings.TEMPLATES["garage"]["variants"]))

print("library resolution (hit -> assetPath; miss/primitive -> pending)")


class FakeLib:
    def __init__(self, source, glb=""):
        self.source, self.glb = source, glb
        self.calls = []

    def resolve(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(source=self.source, glbPath=self.glb,
                               usdzPath="", libraryAssetId="x")


hit = FakeLib("library", "/assets/spatail-library/generated/sedan_car.glb")
s_hit = settings.compose_setting(V8, "exp_lib_hit", library=hit)
check("library hit fills assetPath",
      all(e["assetPath"] == "/assets/spatail-library/generated/sedan_car.glb"
          for e in s_hit["elements"]))
check("resolver got subject + environment role",
      hit.calls and hit.calls[0]["semantic_role"] == "environment"
      and "hood" in hit.calls[0]["subject"])
check("hit leaves generationJobId null (no job needed)",
      all(e["generationJobId"] is None for e in s_hit["elements"]))
check("no pending elements after a full library hit",
      settings.pending_elements(s_hit) == [])

prim = FakeLib("primitive")
s_prim = settings.compose_setting(V8, "exp_lib_prim", library=prim)
check("primitive tier is NOT a real model -> assetPath stays null",
      all(e["assetPath"] is None for e in s_prim["elements"]))
check("pending_elements returns exactly the unresolved props",
      len(settings.pending_elements(s_prim)) == len(s_prim["elements"]) > 0)
check("element_asset_id is a stable subject slug",
      settings.element_asset_id(s_prim["elements"][0]).startswith("sedan_car"))


class BrokenLib:
    def resolve(self, **kw):
        raise RuntimeError("library exploded")


s_broken = settings.compose_setting(V8, "exp_lib_broken", library=BrokenLib())
check("a broken library never kills the setting",
      shape_ok(s_broken) and all(e["assetPath"] is None for e in s_broken["elements"]))

print("LLM hook validator (malformed -> None => template fallback)")
check("non-dict rejected", settings._clean_llm_setting(["nope"], "exp_x") is None)
check("missing elements rejected",
      settings._clean_llm_setting({"why": "w"}, "exp_x") is None)
check("bad footprint rejected", settings._clean_llm_setting(
    {"why": "w", "elements": [{"id": "a", "subject": "thing",
                               "footprintMeters": [1, "wide"],
                               "pose": {"position": [0, 0, 0], "yawDeg": 0}}]},
    "exp_x") is None)
good = settings._clean_llm_setting({
    "why": "A rooftop fits the sky question.",
    "elements": [{"id": "Rail!", "subject": "rooftop railing",
                  "footprintMeters": [2.0, 1.0, 0.1],
                  "pose": {"position": [0, 0, -99], "yawDeg": 400.0}}],
    "experienceAnchor": {"elementId": "Rail!", "name": "Sky View",
                         "position": [0, 1.5, -2], "yawDeg": 0},
    "ground": {"kind": "floor", "sizeMeters": [9, 9]},
    "ambiance": {"style": "3d-pastel", "light": "starlit-night"},
}, "exp_llm")
check("good LLM setting cleans to the exact spec shape",
      good is not None and shape_ok(good) and good["id"].startswith("bespoke-"))
check("LLM positions clamped + yaw normalized",
      good is not None and good["elements"][0]["pose"]["position"][2] == -6.0
      and good["elements"][0]["pose"]["yawDeg"] == 40.0)
check("LLM anchor elementId resolves against slugged element ids",
      good is not None and good["experienceAnchor"]["elementId"] == "rail")
check("cleaned LLM setting validates against the schema",
      good is not None and not trace_store.validate(good, sub))
check("compose_setting(use_llm=False) never emits a bespoke id",
      not settings.compose_setting(V8, "exp_no_llm", use_llm=False)["id"].startswith("bespoke-"))

if FAILURES:
    print("SELFTEST FAIL ({0})".format(FAILURES))
    sys.exit(1)
print("SELFTEST PASS")
