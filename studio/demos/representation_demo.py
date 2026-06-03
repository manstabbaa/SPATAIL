"""representation_demo.py — the SPATAIL Representation Engine acceptance demo.

Runs the five success-criteria prompts end to end and prints, for each:
  domain · intent · strategy · experience beats · Asset Request Manifest ·
  Runtime Scene Contract · placeholder scene · Asset Delivery Package structure ·
  progressive loading plan.

    python studio/demos/representation_demo.py              # all 5, dry-run (no Blender)
    python studio/demos/representation_demo.py --assert     # also assert the triples
    python studio/demos/representation_demo.py --real 0     # build prompt #0 for real
    python studio/demos/representation_demo.py --use-llm    # allow the claude-CLI refine
    python studio/demos/representation_demo.py --out DIR     # also write artifacts to DIR

Dry-run (default) needs neither Blender nor the claude CLI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # studio/

from representation.engine import RepresentationEngine          # noqa: E402
from asset_factory.blender_factory import BlenderAssetFactory   # noqa: E402
from runtime.scene_builder import RuntimeSceneBuilder           # noqa: E402
from runtime.progressive_loader import ProgressiveLoader        # noqa: E402
from runtime.interaction_orchestrator import InteractionOrchestrator  # noqa: E402

# Windows consoles default to cp1252, which can't encode the arrows / box-drawing
# this demo prints — force UTF-8 so output never crashes (matches llm_author's
# UTF-8 discipline for the CLI).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROMPTS = [
    ("Explain a V8 engine on my table.", ("mechanical", "explain", "exploded_view")),
    ("Show me cell division.", ("biological", "demonstrate", "animated_simulation")),
    ("Compare three TVs on my wall.", ("product", "compare", "comparison_layout")),
    ("Show me how a bridge is assembled.", ("architectural", "assemble", "assembly_sequence")),
    ("Explain the solar system in my room.", ("scientific", "explain", "real_scale_placement")),
]


def _h(title: str) -> None:
    print("\n" + "─" * 78 + f"\n  {title}\n" + "─" * 78)


def _j(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def run_prompt(prompt: str, *, real: bool, use_llm: bool, blender_exe: str | None,
               out: Path | None):
    eng = RepresentationEngine(use_llm=use_llm)
    factory = BlenderAssetFactory(blender_exe=blender_exe)
    builder = RuntimeSceneBuilder()
    loader = ProgressiveLoader()
    orch = InteractionOrchestrator()

    plan, manifest = eng.run(prompt)
    hero = plan.requiredAssets[0].id if plan.requiredAssets else None
    pkg = factory.produce(manifest, subject=plan.subject, dry_run=not real,
                          only=[hero] if real else None,
                          placement=asdict(plan.placement))
    contract = builder.build(plan, pkg, scene_id=plan.experienceId, prompt=prompt)
    load_plan = loader.plan_load(plan, contract, pkg)
    placeholder = loader.placeholder_scene(plan, contract)
    gate = orch.gate(load_plan, contract)

    print("\n" + "=" * 78)
    print(f"  PROMPT: {prompt}")
    print("=" * 78)
    print(f"  1. DOMAIN     : {plan.domain}  (confidence {plan.domainConfidence}, {plan.source})")
    print(f"  2. INTENT     : {plan.intent}  (confidence {plan.intentConfidence})")
    print(f"  3. STRATEGY   : {plan.strategy}")
    print(f"     subject    : {plan.subject!r}")
    print(f"     placement  : {plan.placement.anchor}/{plan.placement.layout}/"
          f"{plan.placement.scale_mode}")
    print(f"     reasoning  : {plan.reasoning}")

    _h("4. EXPERIENCE BEATS")
    print(_j([asdict(b) for b in plan.experienceBeats]))

    _h("5. ASSET REQUEST MANIFEST  (Representation Engine → Asset Factory)")
    print(_j(manifest.to_dict()))

    _h("8. ASSET DELIVERY PACKAGE  (Asset Factory → Runtime)  [builds_attempted="
       f"{factory.builds_attempted}]")
    print(_j(pkg.to_dict()))

    _h("6. RUNTIME SCENE CONTRACT")
    print(_j(contract))

    _h("7. PLACEHOLDER SCENE  (phase-0, no Blender)")
    print(_j(placeholder.to_dict()))

    _h("9. PROGRESSIVE LOADING PLAN")
    print(_j(load_plan.to_dict()))

    _h("   INTERACTION GATING")
    print(_j({"gate": gate, "triggers": orch.triggers(contract)}))

    if out:
        out.mkdir(parents=True, exist_ok=True)
        eid = plan.experienceId
        (out / f"{eid}.experience_plan.json").write_text(_j(plan.to_dict()), encoding="utf-8")
        (out / f"{eid}.asset_request_manifest.json").write_text(_j(manifest.to_dict()), encoding="utf-8")
        (out / f"{eid}.delivery_package.json").write_text(_j(pkg.to_dict()), encoding="utf-8")
        (out / f"{eid}.runtime_contract.json").write_text(_j(contract), encoding="utf-8")
        (out / f"{eid}.progressive_plan.json").write_text(_j(load_plan.to_dict()), encoding="utf-8")
        (out / f"{eid}.placeholder_scene.json").write_text(_j(placeholder.to_dict()), encoding="utf-8")
        print(f"\n  [wrote artifacts → {out}]")

    return plan, factory.builds_attempted


def main() -> int:
    ap = argparse.ArgumentParser(description="SPATAIL Representation Engine demo")
    ap.add_argument("--assert", dest="do_assert", action="store_true",
                    help="assert each prompt's (domain, intent, strategy) triple")
    ap.add_argument("--real", type=int, default=None, metavar="INDEX",
                    help="build prompt INDEX (0-4) for real via headless Blender")
    ap.add_argument("--use-llm", action="store_true",
                    help="allow the optional claude-CLI classifier refine")
    ap.add_argument("--blender-exe", default=None, help="path to blender.exe")
    ap.add_argument("--out", default=None, help="also write artifacts to this dir")
    a = ap.parse_args()

    out_root = Path(a.out) if a.out else None
    failures = []
    for i, (prompt, expect) in enumerate(PROMPTS):
        real = a.real == i
        plan, _ = run_prompt(prompt, real=real, use_llm=a.use_llm,
                             blender_exe=a.blender_exe,
                             out=(out_root / f"p{i}") if out_root else None)
        got = (plan.domain, plan.intent, plan.strategy)
        if a.do_assert and got != expect:
            failures.append((prompt, got, expect))

    if a.do_assert:
        _h("ASSERTIONS")
        if failures:
            for prompt, got, expect in failures:
                print(f"  FAIL {prompt}\n       got={got} expect={expect}")
            print(f"\n  {len(failures)}/{len(PROMPTS)} prompts failed")
            return 1
        print(f"  all {len(PROMPTS)} (domain, intent, strategy) triples match ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
