"""demo.py — build an EducationalExperiencePlan for every demo concept, + selftest.

    python studio/educational/demo.py            # print per-concept summary
    python studio/educational/demo.py --assert   # also assert the invariants

Proves the read→select→explain path: each selection becomes a stronger prompt, a
domain/intent/strategy, concept-specific beats, an Asset Request Manifest, a runtime
contract, and a placeholder scene — all library-resolved, no Blender, no LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # studio/
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

from educational import reader                                 # noqa: E402
from educational.experience_plan import build_educational_experience  # noqa: E402

BC_KEYS = {"schemaVersion", "sceneId", "title", "representation", "placement",
           "staging", "beats", "interactions", "assets"}


def main() -> int:
    do_assert = "--assert" in sys.argv
    errs: list[str] = []

    def check(cond, msg):
        if not cond:
            errs.append(msg)

    for c in reader.CONCEPTS:
        sel = c["selectionTargets"][0]
        exp = build_educational_experience(
            sel, c["body"], learning_goal=c["learningGoal"], experience_id=f"edu_{c['id']}",
            beats=c["experienceBeats"], interactions=c["interactions"])
        rc = exp["runtimeContract"]
        ep = exp["experiencePlan"]
        sources = sorted({a.get("source") for a in exp["deliveryPackage"]["assets"]})

        print("=" * 78)
        print(f"  select: {sel!r}   (concept {c['id']})")
        print(f"  goal  : {exp['learningGoal']}")
        print(f"  prompt: {exp['spatailPrompt'][:110]}…")
        print(f"  engine: {ep['domain']} / {ep['intent']} / {ep['strategy']}   "
              f"(preferred {exp['preferredRepresentation']})")
        print(f"  beats : {exp['experienceBeats']}")
        print(f"  assets: {[(a['assetId'], a['source'], a.get('libraryAssetId')) for a in exp['deliveryPackage']['assets']]}")
        print(f"  interactions: {exp['interactions']}")
        print()

        check(exp["mode"] == "educational_wrapper", f"{c['id']}: mode")
        check(exp["conceptId"] == c["id"], f"{c['id']}: conceptId {exp['conceptId']}")
        check(rc.get("mode") == "educational_wrapper" and "educational" in rc, f"{c['id']}: contract not tagged")
        check(sel.lower() in exp["spatailPrompt"].lower(), f"{c['id']}: prompt missing selection")
        check(len(rc["beats"]) == len(c["experienceBeats"]), f"{c['id']}: beats not concept-specific")
        check(not BC_KEYS - set(rc.keys()), f"{c['id']}: contract missing keys {BC_KEYS - set(rc.keys())}")
        check("generate" not in sources, f"{c['id']}: an asset hit 'generate' (library should resolve it): {sources}")

    print(f"checked {len(reader.CONCEPTS)} concepts; {len(errs)} issue(s)")
    for e in errs:
        print("  -", e)
    if do_assert and errs:
        return 1
    print("SELFTEST PASS" if do_assert else "(run with --assert to verify invariants)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
