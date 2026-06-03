"""build_cache.py — pre-generate the cached educational content (no Blender).

    python studio/educational/build_cache.py

Writes, under public/educational/:
  reader.json                 the book + 7 concept pages (the iOS ReadingView content)
  experiences/<concept>.json  a full EducationalExperiencePlan per concept
                              (prompt + plan + manifest + runtime contract + delivery +
                               progressive), all library-resolved — instant, no Blender
  index.json                  concept -> experience path + chosen strategy

These are the cached assets so the demo works the moment the app opens, before any
Blender generation. Idempotent.
"""
from __future__ import annotations

import json
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

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "public" / "educational"
EXP = OUT / "experiences"


def _load_library():
    try:
        from library.asset_library import AssetLibrary
        lib = AssetLibrary()
        return lib if lib.count else None
    except Exception:
        return None


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    EXP.mkdir(parents=True, exist_ok=True)
    lib = _load_library()

    (OUT / "reader.json").write_text(
        json.dumps({"book": reader.BOOK, "concepts": reader.CONCEPTS}, indent=2, ensure_ascii=False),
        encoding="utf-8")

    index = {"mode": "educational_wrapper", "book": reader.BOOK["title"],
             "count": 0, "experiences": []}
    for c in reader.CONCEPTS:
        exp = build_educational_experience(
            c["selectionTargets"][0], c["body"], learning_goal=c["learningGoal"],
            experience_id=f"edu_{c['id']}", beats=c["experienceBeats"],
            interactions=c["interactions"], library=lib)
        (EXP / f"{c['id']}.json").write_text(
            json.dumps(exp, indent=2, ensure_ascii=False), encoding="utf-8")
        index["experiences"].append({
            "conceptId": c["id"], "title": c["title"],
            "selectedText": c["selectionTargets"][0],
            "preferredRepresentation": exp["preferredRepresentation"],
            "strategy": exp["experiencePlan"]["strategy"],
            "domain": exp["experiencePlan"]["domain"],
            "path": f"/educational/experiences/{c['id']}.json",
        })
    index["count"] = len(index["experiences"])
    (OUT / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")

    print(f"[educational] cached {index['count']} experiences -> {EXP}")
    for e in index["experiences"]:
        print(f"    {e['conceptId']:<20} {e['domain']:<14} {e['preferredRepresentation']:<22} -> {e['strategy']}")
    print(f"[educational] reader.json: {len(reader.CONCEPTS)} concept pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
