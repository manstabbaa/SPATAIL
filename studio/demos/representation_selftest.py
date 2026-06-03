"""representation_selftest.py — offline invariant checks for the subsystem.

Runs entirely without Blender or the claude CLI and asserts the contracts the
rest of SPATAIL relies on:

  * the 5 success-criteria prompts classify to the expected (domain,intent,strategy);
  * placement / interaction vocab stays inside the studio Experience Spec enums
    (so a plan could be lowered into a valid es.Experience);
  * the Runtime Scene Contract carries every top-level key contract.build_contract
    emits, and its comfortGuides equal a freshly computed xr_design block;
  * progressive phase-0 (placeholder) is derivable from the Experience Plan ALONE,
    needs no Blender, and the load plan's phase 0 is instant + Blender-free;
  * the AssetCache skip-if-exists logic (register → hit, missing key → miss,
    deleted artifact → invalidate, manifest persists across instances).

    python studio/demos/representation_selftest.py     # exit 0 on success
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # studio/

from representation.engine import RepresentationEngine          # noqa: E402 (bootstraps paths)
import xr_design as xr                                          # noqa: E402
import experience_spec as es                                   # noqa: E402
from asset_factory.asset_cache import AssetCache                # noqa: E402
from asset_factory.blender_factory import BlenderAssetFactory   # noqa: E402
from runtime.progressive_loader import ProgressiveLoader        # noqa: E402
from runtime.scene_builder import RuntimeSceneBuilder           # noqa: E402

for _stream in (sys.stdout, sys.stderr):                        # cp1252-safe output
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

# The top-level keys contract.build_contract (the StudioSceneContract) emits.
BC_KEYS = {"schemaVersion", "createdAt", "sceneId", "title", "sourceBrief",
           "domain", "studio", "comfortGuides", "staging", "beats",
           "storySequence", "interactions", "assets", "xrDesignCitations"}


def _expected_guides(d: float) -> dict:
    eye = xr.EYE_HEIGHT_M
    return {
        "eye_height_m": eye, "cone_deg": xr.OCPA_CONE_DEG,
        "gaze_down_deg": xr.GAZE_DOWN_DEG, "near_clip_m": xr.NEAR_CLIP_M,
        "focal_plane_m": xr.FOCAL_PLANE_M,
        "read_band_m": [xr.READ_NEAR_M, xr.READ_FAR_M], "far_max_m": xr.FAR_MAX_M,
        "focal_point_yup": [0.0, round(xr.baseline_height(xr.FOCAL_PLANE_M), 4),
                            round(-xr.FOCAL_PLANE_M, 4)],
        "stage_distance_m": d, "baseline_z_m": round(xr.baseline_height(d), 4),
    }


def main() -> int:
    eng = RepresentationEngine()
    factory = BlenderAssetFactory()
    builder = RuntimeSceneBuilder()
    loader = ProgressiveLoader()
    errs: list[str] = []

    def check(cond, msg):
        if not cond:
            errs.append(msg)

    for prompt, expect in PROMPTS:
        plan, manifest = eng.run(prompt)
        check((plan.domain, plan.intent, plan.strategy) == expect,
              f"[{prompt}] triple {(plan.domain, plan.intent, plan.strategy)} != {expect}")

        check(plan.placement.anchor in es.ANCHORS, f"[{prompt}] anchor not in es vocab")
        check(plan.placement.layout in es.LAYOUTS, f"[{prompt}] layout not in es vocab")
        check(plan.placement.scale_mode in es.SCALE_MODES, f"[{prompt}] scale_mode not in es vocab")
        check(plan.placement.facing in es.FACINGS, f"[{prompt}] facing not in es vocab")
        for it in plan.interactions:
            check(it.type in es.MECHANIC_TYPES, f"[{prompt}] mechanic {it.type} not in es vocab")

        pkg = factory.produce(manifest, subject=plan.subject, dry_run=True,
                              placement=asdict(plan.placement))
        check(factory.builds_attempted == 0, f"[{prompt}] dry-run invoked Blender")
        contract = builder.build(plan, pkg, scene_id=plan.experienceId, prompt=prompt)

        missing = BC_KEYS - set(contract.keys())
        check(not missing, f"[{prompt}] contract missing build_contract keys {missing}")
        d = builder.stage_distance(plan)
        check(contract["comfortGuides"] == _expected_guides(d),
              f"[{prompt}] comfortGuides != freshly computed xr block")
        for it in contract["interactions"]:
            check(it["type"] in es.MECHANIC_TYPES, f"[{prompt}] contract mechanic not in es vocab")

        # phase-0 derivable from the plan ALONE (no pkg, no contract, no Blender)
        ph = loader.placeholder_scene(eng.plan_only(prompt))
        check(ph.needsBlender is False, f"[{prompt}] phase-0 needs Blender")
        check(bool(ph.title) and bool(ph.firstBeat) and bool(ph.anchor)
              and len(ph.placeholders) >= 1, f"[{prompt}] phase-0 incomplete")

        lp = loader.plan_load(plan, contract, pkg)
        p0 = lp.phases[0]
        check(p0.phase == 0 and p0.blocking and not p0.needsBlender,
              f"[{prompt}] load phase-0 not instant/Blender-free")
        check({"anchor", "placeholder", "title", "first_beat"}.issubset(set(p0.unlocks)),
              f"[{prompt}] load phase-0 unlocks incomplete")
        check(lp.firstInteractiveMs_budget == 1000, f"[{prompt}] first-interactive budget")

    # AssetCache skip-if-exists logic
    with tempfile.TemporaryDirectory() as tmp:
        cache = AssetCache(root=tmp)
        glb = os.path.join(tmp, "x.glb")
        open(glb, "wb").close()
        cache.register(content_key="k1", asset_id="x", kind="t", glb_path=glb,
                       registry_path="r", anim_path="a", bbox_m={"size": [1, 1, 1]}, n_parts=1)
        check(cache.lookup("k1") is not None, "cache: hit after register")
        check(cache.lookup("nope") is None, "cache: miss on unknown key")
        os.remove(glb)
        check(cache.lookup("k1") is None, "cache: invalidate on deleted artifact")
        check(AssetCache(root=tmp)._index.get("k1") is not None, "cache: manifest persisted")

    if errs:
        print(f"SELFTEST FAILED — {len(errs)} issue(s):")
        for e in errs:
            print("  -", e)
        return 1
    print(f"SELFTEST PASS — {len(PROMPTS)} prompts, all invariants hold (no Blender, no CLI).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
