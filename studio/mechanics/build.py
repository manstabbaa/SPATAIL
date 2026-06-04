"""build.py — emit public/mechanics/mechanics.json from catalog.py.

    python studio/mechanics/build.py [--assert]

The manifest is the shared contract: the agent prompt lists these mechanics, and
the iOS / visionOS / web runtimes implement each `runtime` key. Re-run after editing
catalog.py. --assert validates the catalog is internally consistent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "public" / "mechanics" / "mechanics.json"


def _check() -> list[str]:
    errs = []
    seen = set()
    for m in catalog.MECHANICS:
        if m.id in seen:
            errs.append(f"duplicate mechanic id {m.id}")
        seen.add(m.id)
        if m.category not in {"motion", "reveal", "focus", "compare", "annotate",
                              "interact", "camera", "narrate"}:
            errs.append(f"{m.id}: bad category {m.category}")
        for need in m.needs:
            if need not in catalog.CAPABILITIES:
                errs.append(f"{m.id}: unknown capability {need}")
        if m.default_trigger not in m.triggers:
            errs.append(f"{m.id}: default_trigger {m.default_trigger} not in {m.triggers}")
        for pname, spec in m.params.items():
            if spec.get("type") not in catalog.PTYPES:
                errs.append(f"{m.id}.{pname}: bad ptype {spec.get('type')}")
    return errs


def main() -> int:
    errs = _check()
    if errs:
        print("[mechanics] CATALOG ERRORS:")
        for e in errs:
            print("  -", e)
        if "--assert" in sys.argv:
            return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(catalog.catalog_summary(), indent=2), encoding="utf-8")
    cats = catalog.BY_CATEGORY
    print(f"[mechanics] wrote {len(catalog.MECHANICS)} mechanics across {len(cats)} "
          f"categories -> {OUT}")
    for c, ids in cats.items():
        print(f"    {c:<9} {len(ids):>2}  {', '.join(ids)}")
    if "--assert" in sys.argv:
        assert not errs, "catalog invalid"
        print("[mechanics] OK — catalog valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
