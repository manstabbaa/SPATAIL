"""present.py — stage the Meshy-generated segment assets as a SPATAIL experience.

Uses what we already built: the Representation Engine classifies the segment
(domain/intent/strategy/placement), then the Runtime Scene Builder lays the assets
out by xr_design comfort rules and emits the SAME RuntimeSceneContract the iOS
RepresentationRuntime renders — each asset carrying its Meshy USDZ url.

The book paragraph frames the pendulum via everyday examples, so the scene is a
part-to-whole comparison: the simple pendulum (hero) ringed by a clock, a swing and
a sinker, each tappable with a narrated beat.

    python studio/meshy/present.py        # writes public/meshy/pendulum_scene.json (+ index)
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))          # studio/ -> representation, runtime, library, xr_design

import segment                                                    # noqa: E402
from representation.engine import RepresentationEngine            # noqa: E402
from representation.types import (ExperienceBeat, RequiredAsset,  # noqa: E402
                                  InteractionSpec)
from runtime.scene_builder import RuntimeSceneBuilder             # noqa: E402
from library.asset_library import AssetLibrary                    # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "public" / "meshy"

ROLE = {"simple_pendulum": "hero"}            # the rest are comparison_item

# narrated beats straight from the book's framing (focus -> assetId)
BEATS = [
    ("the_pendulum", "A simple pendulum",
     "A small mass — the bob — on a light string, free to swing. For small "
     "displacements it is a simple harmonic oscillator.", "simple_pendulum", "auto"),
    ("in_a_clock", "Crucial uses: the clock",
     "A swinging pendulum keeps a clock's time — its period depends only on length "
     "and gravity, not on how hard it swings.", "pendulum_clock", "on_tap"),
    ("for_fun", "For fun: the swing",
     "A child's swing is a pendulum you can ride — same physics, human scale.",
     "childs_swing", "on_tap"),
    ("just_there", "Just there: the sinker",
     "Even a fishing sinker hanging on a line is a pendulum.", "fishing_sinker", "on_tap"),
    ("all_pendulums", "All the same idea",
     "Clock, swing, sinker — all pendulums, all swinging to the same simple law.",
     "simple_pendulum", "on_tap"),
]


def _delivered(meta: dict, role: str):
    """A DeliveredAsset-shaped object the scene builder can consume."""
    return SimpleNamespace(
        assetId=meta["assetId"], path=meta.get("path", ""),
        status="library", usdzPath=meta.get("usdzPath", ""),
        fallbackPrimitive=meta.get("fallbackPrimitive", "cube"),
        source="meshy", libraryAssetId=meta["assetId"],
        metadata=SimpleNamespace(
            realWorldScale=meta.get("scaleMeters", [0.2, 0.2, 0.2]),
            boundingBoxMeters=meta.get("boundingBoxMeters", {}),
            semanticRole=role),
    )


def build_scene() -> dict:
    lib = AssetLibrary()
    missing = [a["id"] for a in segment.ASSETS if a["id"] not in lib.assets]
    if missing:
        raise SystemExit(f"not yet registered (run pipeline.py first): {missing}")

    # 1) authentic classification via the existing engine
    eng = RepresentationEngine()
    plan, _ = eng.run("Explain the simple pendulum and the everyday objects that are "
                      "pendulums — a clock, a child's swing, a fishing sinker",
                      experience_id="meshy_pendulum_demo",
                      strategy_override="part_to_whole_explanation")

    # 2) override the plan's assets/beats/interactions with our segment + Meshy models
    plan.subject = "the simple pendulum"
    plan.placement.anchor = "table"
    plan.placement.layout = "arc"
    plan.requiredAssets = [
        RequiredAsset(id=a["id"], type=ROLE.get(a["id"], "comparison_item"),
                      priority=(0 if a["id"] == "simple_pendulum" else 1))
        for a in segment.ASSETS]
    plan.experienceBeats = [ExperienceBeat(id=i, title=t, narration=n, focus=f, reveal=r)
                            for (i, t, n, f, r) in BEATS]
    plan.interactions = [InteractionSpec(id=f"reveal_{a['id']}", type="tap_reveal",
                                         label=a["name"], params={"target": a["id"]})
                         for a in segment.ASSETS]

    # 3) delivery package = the registered Meshy library assets, in segment order
    pkg = SimpleNamespace(
        assets=[_delivered(lib.assets[a["id"]], ROLE.get(a["id"], "comparison_item"))
                for a in segment.ASSETS],
        animations=[])

    # 4) the runtime contract the iOS app already renders
    contract = RuntimeSceneBuilder().build(
        plan, pkg, scene_id="meshy_pendulum_demo", prompt=segment.SEGMENT_TEXT)
    contract["mode"] = "meshy_image_to_3d"
    contract["source"] = {
        "pipeline": "gemini_multiview -> meshy_multi_image_to_3d -> blender_normalize -> library",
        "book": segment.CITATION, "segmentText": segment.SEGMENT_TEXT,
        "assets": {a["id"]: {"name": a["name"], "complexity": a["complexity"]}
                   for a in segment.ASSETS},
    }
    return contract


def main() -> int:
    contract = build_scene()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scene_path = OUT_DIR / "pendulum_scene.json"
    scene_path.write_text(json.dumps(contract, indent=2), encoding="utf-8")
    index = {
        "mode": "meshy_image_to_3d",
        "book": segment.CITATION,
        "scenes": [{"id": contract["sceneId"], "title": contract["title"],
                    "url": "/meshy/pendulum_scene.json",
                    "assetCount": len(contract["assets"]),
                    "strategy": contract["representation"]["strategy"]}],
    }
    (OUT_DIR / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"[present] scene '{contract['title']}' — {len(contract['assets'])} assets, "
          f"strategy={contract['representation']['strategy']}, "
          f"anchor={contract['placement']['anchor']}/{contract['placement']['layout']}")
    for a in contract["assets"]:
        print(f"    {a['id']:<18} role={a['role']:<17} usdz={'yes' if a['usdzUrl'] else 'NO':<3} "
              f"pos={a['position_yup']}")
    print(f"[present] wrote {scene_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
