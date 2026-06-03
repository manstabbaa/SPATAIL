"""demo.py — show the starter library resolving the showcase prompts, + selftest.

    python studio/library/demo.py            # print resolutions
    python studio/library/demo.py --assert   # also assert the invariants

Runs the Representation Engine for the success-criteria prompts and, for each asset
the engine requests, prints which library tier resolves it (library / primitive /
placeholder / generate) and the proxy shape + real-world scale the runtime would use.
Proves the library turns "v8_engine" into a cylinder-ish engine block at metres scale
rather than a generic box — with no Blender.
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

from representation.engine import RepresentationEngine          # noqa: E402
from library.asset_library import AssetLibrary                  # noqa: E402

PROMPTS = [
    "Explain a V8 engine on my table.",
    "Show me cell division.",
    "Compare three TVs on my wall.",
    "Show me how a bridge is assembled.",
    "Explain gravity with a falling apple.",
    "Explain the solar system in my room.",
]


def main() -> int:
    do_assert = "--assert" in sys.argv
    lib = AssetLibrary()
    eng = RepresentationEngine()
    errs: list[str] = []

    print(f"[library] loaded {lib.count} assets, {len(lib.strategies)} strategies\n")
    if lib.count < 300:
        errs.append(f"library only loaded {lib.count} assets (expected 300+)")

    for prompt in PROMPTS:
        plan, manifest = eng.run(prompt)
        print("=" * 76)
        print(f"  {prompt}")
        print(f"  {plan.domain} / {plan.intent} / {plan.strategy}  · subject {plan.subject!r}")
        print("=" * 76)
        hero_source = None
        for i, req in enumerate(manifest.assetRequests):
            r = lib.resolve(asset_id=req.assetId, subject=plan.subject, domain=plan.domain,
                            semantic_role=req.semanticRole, strategy=plan.strategy)
            if i == 0:
                hero_source = r.source
            tag = f"{r.source}" + (f"->{r.libraryAssetId}" if r.libraryAssetId else "")
            print(f"    {req.assetId:<26} {tag:<42} {r.fallbackPrimitive:<10} {r.scaleMeters}")
            if r.source not in ("library", "primitive", "placeholder", "generate"):
                errs.append(f"{req.assetId}: bad source {r.source}")
        # the hero (subject itself) should land a semantic match, not a placeholder
        if do_assert and hero_source not in ("library", "primitive"):
            errs.append(f"[{prompt}] hero resolved to {hero_source} (expected library/primitive)")
        print()

    # spot-check find_asset directly
    for tags, dom, expect_cat in [
        (["piston"], "mechanical", "mechanical"),
        (["tv"], "product", "product-preview"),
        (["earth", "planet"], "scientific", "astronomy"),
        (["neuron"], "biological", "biology"),
        (["bridge", "pillar"], "architectural", "architecture"),
    ]:
        m = lib.find_asset(domain=dom, semantic_tags=tags)
        got = m.asset["category"] if m.asset else None
        ok = got == expect_cat
        print(f"  find_asset({tags}, {dom}) -> {m.asset['assetId'] if m.asset else None} "
              f"[{got}] {'OK' if ok else 'EXPECTED ' + expect_cat}")
        if do_assert and not ok:
            errs.append(f"find_asset {tags}/{dom} -> {got}, expected {expect_cat}")

    # factory integration: the library feeds the Blender Asset Factory (no Blender)
    from asset_factory.blender_factory import BlenderAssetFactory
    from dataclasses import asdict
    factory = BlenderAssetFactory(library=lib)
    plan, manifest = eng.run("Explain a V8 engine on my table.")
    pkg = factory.produce(manifest, subject=plan.subject, dry_run=True,
                          placement=asdict(plan.placement), domain=plan.domain)
    print("\n  factory(V8, library attached, dry-run):")
    for a in pkg.assets:
        print(f"    {a.assetId:<26} source={a.source:<10} lib={a.libraryAssetId or '-':<22} "
              f"primitive={a.fallbackPrimitive or '-'}")
        if do_assert and a.source == "generate":
            errs.append(f"factory {a.assetId}: source 'generate' (library should resolve it without Blender)")
    if do_assert and factory.builds_attempted != 0:
        errs.append("factory invoked Blender in dry-run")

    # every asset's representationUses must be known strategies (registry view)
    strat_ids = {s["id"] for s in lib.strategies}
    for aid, a in lib.assets.items():
        for u in a.get("representationUses", []):
            if u not in strat_ids:
                errs.append(f"{aid}: representationUse {u!r} not in strategies.json")

    print()
    if errs:
        print(f"SELFTEST FAILED — {len(errs)} issue(s):")
        for e in errs[:40]:
            print("  -", e)
        return 1
    print("SELFTEST PASS" if do_assert else "(run with --assert to verify invariants)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
