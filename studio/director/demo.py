"""demo.py — prove text -> modular experience (agent-composed mechanics).

    python studio/director/demo.py            # deterministic (offline) + Gemini agent
    python studio/director/demo.py --assert    # validate + exit non-zero on failure
    python studio/director/demo.py --no-llm     # deterministic only

Prints the beats each composer designed and asserts every mechanic is catalog-valid.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mechanics"))
import catalog as mech          # noqa: E402
import experience as ex         # noqa: E402

PROMPTS = [
    "Explain how a simple pendulum swings.",
    "Show me the solar system.",
    "How does the human heart pump blood?",
]


def _check(contract) -> list[str]:
    errs = []
    ids = {a["id"] for a in contract["assets"]} | {"scene"}
    if contract["schemaVersion"] != ex.SCHEMA:
        errs.append("bad schemaVersion")
    if not contract["beats"]:
        errs.append("no beats")
    for b in contract["beats"]:
        for mc in b["mechanics"]:
            if mc["mechanic"] not in mech.BY_ID:
                errs.append(f"{b['id']}: unknown mechanic {mc['mechanic']}")
            if mc["target"] not in ids:
                errs.append(f"{b['id']}: bad target {mc['target']}")
            tr = mech.BY_ID.get(mc["mechanic"])
            if tr and mc["trigger"] not in tr.triggers:
                errs.append(f"{b['id']}: {mc['mechanic']} bad trigger {mc['trigger']}")
    return errs


def main() -> int:
    use_llm = "--no-llm" not in sys.argv
    do_assert = "--assert" in sys.argv
    all_errs = []
    for prompt in PROMPTS:
        c = ex.build_modular_experience(prompt, use_llm=use_llm)
        errs = _check(c)
        all_errs += errs
        print("\n" + "=" * 78)
        print(f"  {prompt}")
        print(f"  domain={c['understanding']['domain']} intent={c['understanding']['intent']} "
              f"subject='{c['understanding']['subject']}'  composer={c['composer']}")
        print(f"  assets: {[a['id'] for a in c['assets']]}")
        print(f"  mechanicsUsed ({len(c['mechanicsUsed'])}): {', '.join(c['mechanicsUsed'])}")
        print("  beats:")
        for b in c["beats"]:
            ms = ", ".join(f"{m['mechanic']}->{m['target']}({m['trigger']})" for m in b["mechanics"])
            print(f"    [{b['id']}] {b['title']}: {ms}")
        print(f"  check: {'OK' if not errs else errs}")
    print("\n" + ("SELFTEST PASS" if not all_errs else f"SELFTEST FAIL: {all_errs}"))
    if do_assert and all_errs:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
