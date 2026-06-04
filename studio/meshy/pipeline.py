"""pipeline.py — orchestrate the image-to-3D path end to end.

    book segment -> Gemini multi-view images -> Meshy multi-image-to-3D ->
    Blender normalize (scale + RealityKit USDZ) -> SPATAIL library register.

    python studio/meshy/pipeline.py                  # meshy -> normalize -> register
    python studio/meshy/pipeline.py --images         # (re)generate Gemini views first
    python studio/meshy/pipeline.py --only simple_pendulum childs_swing

Needs MESHY_API_KEY + GEMINI_API_KEY in ~/.spatail/secrets.env (never committed).
Registered assets land in the SAME library the runtime already resolves from, tagged
source=meshy, so the existing representation/educational presentation picks them up.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))            # config, segment, meshy_client, gemini_images
sys.path.insert(0, str(HERE.parent))     # studio/ -> library.asset_library

import segment            # noqa: E402
import meshy_client       # noqa: E402
from library.asset_library import AssetLibrary  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "studio" / "out" / "meshy"
LIB_DIR = REPO / "public" / "assets" / "spatail-library" / "meshy"
LIB_URL = "/assets/spatail-library/meshy"
GEN_JSON = REPO / "public" / "assets" / "spatail-library" / "manifests" / "generated.json"
BLENDER_EXE = os.environ.get(
    "BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")
CATEGORY = "physics"
_STOP = {"a", "an", "the", "of", "with", "and", "from", "its", "on", "to", "small",
         "single", "by", "at", "near", "that", "has", "also", "known", "as"}


def _tags(a: dict) -> list[str]:
    words = re.split(r"[^a-z0-9]+", f"{a['id']} {a['name']} {a['desc']}".lower())
    seen: list[str] = []
    for w in words:
        if len(w) > 2 and w not in _STOP and w not in seen:
            seen.append(w)
    return seen[:14]


def stage_images(assets):
    import gemini_images
    for a in assets:
        gemini_images.multiview(a)
        print(f"[pipeline] images: {a['id']}")


def stage_meshy(assets) -> dict:
    glb_map = {}
    spent = 0
    for a in assets:
        cached = OUT / a["id"] / "meshy" / f"{a['id']}.glb"
        if cached.exists():
            print(f"[pipeline] {a['id']}: reuse cached Meshy GLB (no credits spent)")
            glb_map[a["id"]] = str(cached); continue
        views = sorted((OUT / a["id"]).glob("view_*.png"))
        if not views:
            print(f"[pipeline] {a['id']}: no views (run with --images)"); continue
        info = meshy_client.generate_3d([str(v) for v in views], a["id"])
        spent += info.get("consumed_credits") or 0
        glb = info.get("files", {}).get("glb", {}).get("path")
        if info.get("status") == "SUCCEEDED" and glb:
            glb_map[a["id"]] = glb
        else:
            print(f"[pipeline] {a['id']}: Meshy {info.get('status')}: {info.get('error')}")
    print(f"[pipeline] Meshy consumed ~{spent} credits for {len(glb_map)} models")
    return glb_map


def stage_normalize(glb_map, assets) -> dict:
    spec_assets = [{
        "assetId": a["id"], "category": CATEGORY, "glb_in": glb_map[a["id"]],
        "scaleMeters": a["scaleMeters"], "pivot": "center_bottom", "out_dir": str(LIB_DIR),
    } for a in assets if a["id"] in glb_map]
    if not spec_assets:
        return {"done": [], "failed": []}
    if not os.path.exists(BLENDER_EXE):
        print(f"[pipeline] Blender not found at {BLENDER_EXE!r} (set BLENDER_EXE)")
        return {"done": [], "failed": [{"assetId": s["assetId"], "error": "no blender"} for s in spec_assets]}
    res_path = OUT / "_normalize_result.json"
    fd, sp = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"result_path": str(res_path), "assets": spec_assets}, f)
    try:
        proc = subprocess.run(
            [BLENDER_EXE, "--background", "--python", str(HERE / "meshy_normalize.py"), "--", sp],
            capture_output=True, text=True, timeout=1800)
        if not res_path.exists():
            print("[pipeline] normalize produced no result; tail:\n", (proc.stderr or proc.stdout)[-1500:])
    finally:
        try: os.remove(sp)
        except OSError: pass
    return json.loads(res_path.read_text(encoding="utf-8")) if res_path.exists() else {"done": [], "failed": []}


def stage_register(normalized, assets) -> list:
    reg = AssetLibrary()
    by_id = {a["id"]: a for a in assets}
    registered = []
    for d in normalized.get("done", []):
        aid = d["assetId"]; a = by_id[aid]
        usdz_url = f"{LIB_URL}/{aid}.usdz" if d.get("usdz") else ""
        reg.register_generated(
            aid, f"{LIB_URL}/{aid}.glb", category=CATEGORY, name=a["name"],
            semantic_tags=_tags(a), scale_meters=a["scaleMeters"], pivot="center_bottom",
            representation_uses=["real_scale_placement", "part_to_whole_explanation",
                                 "side_by_side_comparison"],
            usdz_path=usdz_url, fallback_primitive="cube",
            placement_types=["table", "floor"], bbox_m={"size": a["scaleMeters"]},
            asset_state="generated")
        registered.append(aid)
    # provenance: mark these as Meshy-sourced in the overlay
    if registered and GEN_JSON.exists():
        d = json.loads(GEN_JSON.read_text(encoding="utf-8"))
        for asset in d.get("assets", []):
            if asset["assetId"] in registered:
                asset["source"] = "meshy"
                asset["qualityLevel"] = "meshy_image_to_3d"
        GEN_JSON.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return registered


def main() -> int:
    args = sys.argv[1:]
    do_images = "--images" in args
    only = None
    if "--only" in args:
        i = args.index("--only")
        only = set(args[i + 1:])
    assets = [a for a in segment.ASSETS if (not only or a["id"] in only)]

    LIB_DIR.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    if do_images:
        stage_images(assets)
    glb_map = stage_meshy(assets)
    normalized = stage_normalize(glb_map, assets)
    registered = stage_register(normalized, assets)

    result = {
        "segment": segment.CITATION, "segmentText": segment.SEGMENT_TEXT,
        "assets": [a["id"] for a in assets], "meshyModels": list(glb_map),
        "normalized": [d["assetId"] for d in normalized.get("done", [])],
        "normalizeFailed": normalized.get("failed", []),
        "registered": registered, "ts": int(time.time()),
    }
    (OUT / "pipeline_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"[pipeline] DONE — meshy:{len(glb_map)} normalized:{len(result['normalized'])} "
          f"registered:{len(registered)} -> {OUT/'pipeline_result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
