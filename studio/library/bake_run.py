"""bake_run.py — orchestrate the headless bake (real GLB+USDZ into the library).

    python studio/library/bake_run.py

Builds the bake spec (all universal primitives + a curated set of domain assets the
general-science reader's 15 concepts + the showcase prompts use), runs bake_assets.py
in one headless Blender session, then registers each baked asset into the library's
generated.json overlay (assetState=generated + real glb/usdz paths). After this,
resolve() returns these as real `library` hits and Blender is only invoked for assets
not in the library. Idempotent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # studio/
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

from library.asset_library import AssetLibrary                 # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
BLENDER_EXE = os.environ.get(
    "BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")
BAKE_SCRIPT = Path(__file__).resolve().parent / "bake_assets.py"
LIB_URL = "/assets/spatail-library"

# named domain assets worth caching (what the 15 reader concepts + 5 showcase prompts use)
CURATED = [
    "engine_block_simplified", "cylinder_block_simplified", "piston", "connecting_rod",
    "crankshaft", "camshaft", "valve", "spark_plug", "gear_small", "gear_medium",
    "gear_large", "gear_pair", "shaft", "bolt", "spring", "fan_blade", "turbine_wheel",
    "pulley", "bearing", "brake_disc",
    "generic_cell", "nucleus", "mitochondria", "dna_double_helix", "chromosome", "neuron",
    "heart_simplified", "red_blood_cell", "virus_particle", "atom_nucleus", "electron_orbit",
    "molecule_ball", "water_molecule",
    "apple", "ramp", "pendulum", "lever", "fulcrum", "weight_block", "force_arrow",
    "magnetic_bar", "prism", "beaker", "spring_mass_system", "inclined_plane",
    "sun", "mercury", "venus", "earth", "moon", "mars", "jupiter", "saturn", "uranus",
    "neptune", "orbit_ring", "asteroid",
    "bridge_deck", "bridge_pillar", "suspension_cable", "cable_stayed_tower",
    "foundation_block", "truss_segment", "column", "beam", "steel_i_beam", "floor_slab",
    "arch", "brick", "traffic_cone", "wall_segment", "colosseum", "skyscraper",
    "tv_55_inch", "phone", "laptop", "monitor", "speaker", "chair", "sofa", "desk",
    "bar_chart_bar", "axis_line", "percentage_ring",
]


def main() -> int:
    lib = AssetLibrary()
    ids = list(lib.by_category.get("primitives", []))
    ids += [a for a in CURATED if a in lib.assets]
    seen, spec_assets = set(), []
    for aid in ids:
        if aid in seen:
            continue
        seen.add(aid)
        a = lib.assets[aid]
        if a.get("fallbackPrimitive") == "none":
            continue
        spec_assets.append({
            "assetId": aid, "category": a["category"],
            "fallbackPrimitive": a.get("fallbackPrimitive", "cube"),
            "scaleMeters": a.get("scaleMeters", [0.2, 0.2, 0.2]),
            "pivot": a.get("pivot", "center_bottom"),
        })

    # Master File pivot (2026): GLB is canonical (Android XR native). USDZ is the
    # iOS reference target only — opt in with --ios (or --targets glb,usdz).
    targets = ["glb"]
    for i, a in enumerate(sys.argv):
        if a == "--ios":
            targets = ["glb", "usdz"]
        elif a == "--targets" and i + 1 < len(sys.argv):
            targets = [t.strip().lower() for t in sys.argv[i + 1].split(",") if t.strip()]
    if "ios" in targets and "usdz" not in targets:
        targets.append("usdz")

    result_path = REPO_ROOT / "studio" / "out" / "bake_result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)

    if "--register-only" in sys.argv:                          # skip Blender; re-register
        if not result_path.exists():
            print("[bake] --register-only but no bake_result.json found")
            return 1
        res = json.loads(result_path.read_text(encoding="utf-8"))
        print(f"[bake] register-only: {res['n_baked']} baked assets from {result_path.name}")
    else:
        spec = {"repoRoot": str(REPO_ROOT), "result_path": str(result_path),
                "targets": targets, "assets": spec_assets}
        print(f"[bake] targets: {', '.join(targets)}")
        fd, spec_path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        if not os.path.exists(BLENDER_EXE):
            print(f"[bake] Blender not found at {BLENDER_EXE!r} (set BLENDER_EXE).")
            return 1
        print(f"[bake] baking {len(spec_assets)} assets in one headless Blender session…")
        try:
            proc = subprocess.run(
                [BLENDER_EXE, "--background", "--python", str(BAKE_SCRIPT), "--", spec_path],
                capture_output=True, text=True, timeout=1800)
        finally:
            try:
                os.remove(spec_path)
            except OSError:
                pass
        if not result_path.exists():
            print("[bake] NO result sidecar — Blender output tail:")
            print((proc.stderr or proc.stdout or "")[-2500:])
            return 1
        res = json.loads(result_path.read_text(encoding="utf-8"))
        print(f"[bake] Blender finished: {res['n_baked']} baked, {res['n_failed']} failed")

    reg = AssetLibrary()
    for b in res["baked"]:
        a = lib.assets[b["assetId"]]
        cat, aid = b["category"], b["assetId"]
        usdz_url = f"{LIB_URL}/{cat}/{aid}.usdz" if b.get("usdz") else ""
        reg.register_generated(
            aid, f"{LIB_URL}/{cat}/{aid}.glb", category=cat, name=a.get("name", aid),
            semantic_tags=a.get("semanticTags", []), scale_meters=a.get("scaleMeters"),
            pivot=a.get("pivot", "center_bottom"),
            representation_uses=a.get("representationUses", []), usdz_path=usdz_url,
            fallback_primitive=a.get("fallbackPrimitive", "cube"),
            placement_types=a.get("placementTypes", ["table", "floor"]),
            bbox_m={"size": a.get("scaleMeters")}, asset_state="generated")
    print(f"[bake] registered {res['n_baked']} assets into generated.json")
    for f in res["failed"][:25]:
        print(f"    FAILED {f['assetId']}: {f['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
