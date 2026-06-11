"""asset_service.py — the LIVE asset artist: subject → Meshy asset → phone-ready USDZ.

The DEFAULT asset path of the content pipeline (tracked/placed pivot, 2026-06):

    subject/brief → Gemini multi-view reference images → Meshy multi-image-to-3D
    → headless-Blender NORMALIZE (real scale, metres/Y-up, library exporters)
    → headless-Blender DECIMATE (mobile budget: ~12K tris, 1K textures)
    → copy into the job's artifacts dir ({job_id}.usdz — the phone's existing
      generationJobId polling streams it in over the placeholder)
    → register in the SPATAIL library (the NEXT request for this subject is an
      instant library hit; the library compounds).

job_server's object worker calls produce() FIRST and falls back to the procedural
Blender author on ANY exception, so this module may fail freely. Blender (the
scene constructor/animator) takes the asset from here — rig/clip authoring on top
of Meshy meshes is the next stage of the pipeline, not this module's job.

Caching (idempotent, credit-safe): everything keys on the asset slug under
studio/out/meshy/<slug>/ — Gemini views and the downloaded Meshy GLB are reused
on re-runs, so repeating a subject costs 0 credits and only re-runs Blender.

Knobs:
    SPATAIL_ASSET_PATH=meshy|procedural   default meshy (when both API keys exist)
    SPATAIL_MESHY_DECIMATE=1|0            default 1 (ship the _ar.usdz budget cut)
    SPATAIL_MESHY_POLL_S=540              Meshy task poll timeout (seconds)
    BLENDER_EXE                           headless Blender (defaults to 5.1 path)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent)):          # config/meshy_client/gemini_images, library.*
    if p not in sys.path:
        sys.path.insert(0, p)

import config         # noqa: E402
import meshy_client   # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "studio" / "out" / "meshy"
LIB_DIR = REPO / "public" / "assets" / "spatail-library" / "meshy"
LIB_URL = "/assets/spatail-library/meshy"
BLENDER_EXE = os.environ.get(
    "BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")

DEFAULT_SCALE_M = [0.4, 0.4, 0.4]
AR_TRIS = int(os.environ.get("SPATAIL_MESHY_TRIS", "12000"))
AR_TEX = int(os.environ.get("SPATAIL_MESHY_TEX", "1024"))


def available() -> bool:
    """Meshy is the default path when enabled and both API keys + Blender exist."""
    if os.environ.get("SPATAIL_ASSET_PATH", "meshy").strip().lower() == "procedural":
        return False
    return bool(config.get("MESHY_API_KEY") and config.get("GEMINI_API_KEY")
                and os.path.exists(BLENDER_EXE))


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (s or "asset").lower()).strip("_")
    return s[:48] or "asset"


def _desc(subject: str, brief: str) -> str:
    """The object description Gemini renders. Subject leads; the brief adds detail
    but is capped — Gemini needs ONE photographable object, not a lecture."""
    subject = (subject or "").strip()
    brief = (brief or "").strip()
    if brief and brief.lower() != subject.lower():
        return f"a {subject} — {brief}"[:300] if subject else brief[:300]
    return f"a {subject}"[:300] if subject else "an object"


def _run_blender(script: Path, spec: dict, result_path: Path, timeout: int = 1200) -> dict:
    """Run a studio/meshy bpy script headless against a spec file; return its result."""
    if result_path.exists():
        result_path.unlink()
    fd, sp = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(spec, f)
    try:
        proc = subprocess.run(
            [BLENDER_EXE, "--background", "--python", str(script), "--", sp],
            capture_output=True, text=True, timeout=timeout)
        if not result_path.exists():
            tail = (proc.stderr or proc.stdout or "")[-1200:]
            raise RuntimeError(f"{script.name} produced no result; tail: {tail}")
    finally:
        try:
            os.remove(sp)
        except OSError:
            pass
    return json.loads(result_path.read_text(encoding="utf-8"))


def _register(slug: str, name: str, desc: str, scale_m: list, category: str,
              glb_url: str, usdz_url: str) -> None:
    """Best-effort library registration so the next prompt resolves this asset
    directly (no generation job at all). Failure here never fails the build."""
    try:
        from library.asset_library import AssetLibrary
        words = re.split(r"[^a-z0-9]+", f"{slug} {name} {desc}".lower())
        tags, seen = [], set()
        for w in words:
            if len(w) > 2 and w not in seen:
                seen.add(w)
                tags.append(w)
        AssetLibrary().register_generated(
            slug, glb_url, category=category or "general",
            name=name.strip().title() or slug, semantic_tags=tags[:14],
            scale_meters=scale_m, pivot="center_bottom",
            representation_uses=["real_scale_placement", "part_to_whole_explanation"],
            usdz_path=usdz_url, fallback_primitive="cube",
            placement_types=["table", "floor"], bbox_m={"size": scale_m},
            asset_state="generated")
        print(f"[asset] registered {slug} in the library -> {usdz_url}")
    except Exception as exc:  # noqa: BLE001
        print(f"[asset] library registration skipped: {exc}")


def produce(subject: str, brief: str, job_id: str, artifacts_dir, *,
            asset_id: str | None = None, scale_meters: list | None = None,
            category: str = "general",
            on_stage=lambda s: None) -> dict:
    """Full chain. Returns the SAME shape generator/headless_build return
    ({usdz_name, metadata_name, max_dim, spec}) so job_server's done-handling and
    the phone's polling work unchanged. Raises on failure → caller falls back."""
    t0 = time.time()
    artifacts_dir = Path(artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(asset_id or subject)
    scale_m = list(scale_meters or DEFAULT_SCALE_M)
    desc = _desc(subject, brief)
    asset_out = OUT / slug
    asset_out.mkdir(parents=True, exist_ok=True)
    credits = 0

    # 1) Gemini multi-view reference images (cached per slug)
    views = sorted(asset_out.glob("view_*.png"))
    if len(views) < 2:
        on_stage("imagining the object (multi-view)")
        import gemini_images
        gemini_images.multiview({"id": slug, "desc": desc})
        views = sorted(asset_out.glob("view_*.png"))
    if not views:
        raise RuntimeError("no reference views generated")

    # 2) Meshy multi-image-to-3D (cached GLB per slug → re-runs spend 0 credits)
    glb_in = asset_out / "meshy" / f"{slug}.glb"
    if not glb_in.exists():
        on_stage("sculpting the 3D model (Meshy)")
        poll_s = int(os.environ.get("SPATAIL_MESHY_POLL_S", "540"))
        info = meshy_client.generate_3d([str(v) for v in views], slug,
                                        **{"target_polycount": 30000})
        credits = info.get("consumed_credits") or 0
        if info.get("status") != "SUCCEEDED" or "glb" not in info.get("files", {}):
            raise RuntimeError(f"Meshy {info.get('status')}: {info.get('error')}")
        glb_in = Path(info["files"]["glb"]["path"])
        _ = poll_s  # poll timeout currently fixed inside meshy_client.poll
    else:
        print(f"[asset] {slug}: reusing cached Meshy GLB (0 credits)")

    # 3) Normalize to real scale (metres/Y-up, library exporters) → LIB_DIR
    on_stage("bringing it to real scale")
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    norm = _run_blender(
        HERE / "meshy_normalize.py",
        {"result_path": str(OUT / f"_{slug}_normalize.json"),
         "assets": [{"assetId": slug, "category": category, "glb_in": str(glb_in),
                     "scaleMeters": scale_m, "pivot": "center_bottom",
                     "out_dir": str(LIB_DIR)}]},
        OUT / f"_{slug}_normalize.json")
    if not norm.get("done"):
        raise RuntimeError(f"normalize failed: {norm.get('failed')}")

    # 4) Decimate to the mobile budget (~12K tris / 1K textures) → <slug>_ar.usdz
    ship_usdz = LIB_DIR / f"{slug}.usdz"
    if os.environ.get("SPATAIL_MESHY_DECIMATE", "1").lower() not in ("0", "false", "no"):
        on_stage("optimizing for AR")
        try:
            dec = _run_blender(
                HERE / "decimate.py",
                {"result_path": str(OUT / f"_{slug}_decimate.json"),
                 "target_tris": AR_TRIS, "texture_max": AR_TEX,
                 "assets": [{"assetId": slug, "glb_in": str(LIB_DIR / f"{slug}.glb"),
                             "out_dir": str(LIB_DIR)}]},
                OUT / f"_{slug}_decimate.json")
            ar = LIB_DIR / f"{slug}_ar.usdz"
            if dec.get("done") and ar.exists():
                ship_usdz = ar
        except Exception as exc:  # noqa: BLE001
            print(f"[asset] decimate skipped ({exc}) — shipping full-res USDZ")
    if not ship_usdz.exists():
        raise RuntimeError("no USDZ produced")

    # 5) Publish into the job's artifacts (existing phone polling streams it in)
    on_stage("publishing")
    usdz_name = f"{job_id}.usdz"
    shutil.copyfile(ship_usdz, artifacts_dir / usdz_name)
    # library URLs follow what we actually ship: the decimated _ar cut when it
    # exists (so library hits stream the mobile budget, not the 12–22 MB original)
    shipped_ar = ship_usdz.name.endswith("_ar.usdz")
    lib_glb = f"{LIB_URL}/{slug}_ar.glb" if (shipped_ar and (LIB_DIR / f"{slug}_ar.glb").exists()) \
        else f"{LIB_URL}/{slug}.glb"
    lib_usdz = f"{LIB_URL}/{ship_usdz.name}"
    meta = {
        "source": "meshy", "subject": subject, "desc": desc, "assetId": slug,
        "scaleMeters": scale_m, "credits": credits,
        "usdzBytes": (artifacts_dir / usdz_name).stat().st_size,
        "libraryGlb": lib_glb, "libraryUsdz": lib_usdz,
        "elapsedS": round(time.time() - t0, 1),
    }
    metadata_name = f"{job_id}_metadata.json"
    (artifacts_dir / metadata_name).write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # 6) Library registration → the next prompt for this subject skips generation
    _register(slug, subject, desc, scale_m, category, lib_glb, lib_usdz)

    print(f"[asset] {slug}: meshy asset ready in {meta['elapsedS']}s "
          f"({meta['usdzBytes'] / 1e6:.1f} MB, {credits} credits)")
    return {"usdz_name": usdz_name, "metadata_name": metadata_name,
            "max_dim": max(scale_m), "spec": {"source": "meshy", "assetId": slug},
            "bbox_yup": {"size": scale_m}}


if __name__ == "__main__":
    # smoke run:  python studio/meshy/asset_service.py "fire extinguisher" [assetId]
    subj = sys.argv[1] if len(sys.argv) > 1 else "fire extinguisher"
    aid = sys.argv[2] if len(sys.argv) > 2 else None
    print(f"available={available()}")
    res = produce(subj, subj, "svc_test", REPO / "studio" / "out" / "gen",
                  asset_id=aid, on_stage=lambda s: print(f"  stage: {s}"))
    print(json.dumps(res, indent=2))
