"""demo.py — prove text -> modular experience (a clip SEQUENCE the runtime plays).

    python studio/director/demo.py            # deterministic + Gemini sequencer
    python studio/director/demo.py --no-llm    # deterministic only
    python studio/director/demo.py --assert     # exit non-zero on failure

Motion is baked into each asset as clips; the composer SEQUENCES them (no catalog).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import experience as ex          # noqa: E402

PROMPTS = [
    "Explain how a simple pendulum swings.",
    "Show me the solar system.",
    "How does a v8 engine work?",
]


def main() -> int:
    use_llm = "--no-llm" not in sys.argv
    do_assert = "--assert" in sys.argv
    errs: list[str] = []
    for prompt in PROMPTS:
        c = ex.build_modular_experience(prompt, use_llm=use_llm)
        print("\n" + "=" * 74)
        print(f"  {prompt}")
        print(f"  composer={c['composer']}  schema={c['schemaVersion']}")
        print(f"  assets: {[a['id'] for a in c['assets']]}")
        print(f"  clips: {[cl['name'] for cl in c.get('clips', [])]}")
        print("  sequence:")
        for s in c.get("sequence", []):
            print(f"    [{s['id']}] {s['title']}: play '{s['clip']}' on {s['focus']}")
        print(f"  triggers: {len(c.get('triggers', []))}")
        if not c.get("sequence"):
            errs.append(f"{prompt}: no sequence")
        if "beats" in c or "mechanicsUsed" in c:
            errs.append(f"{prompt}: legacy mechanics fields still present")
    print("\n" + ("SELFTEST PASS" if not errs else f"SELFTEST FAIL: {errs}"))
    return 1 if (do_assert and errs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
