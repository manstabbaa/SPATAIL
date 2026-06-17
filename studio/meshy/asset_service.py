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
    SPATAIL_MESHY_ANCHORS=1|0             default 1 (Blender-computed surface anchors)
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
for p in (str(HERE), str(HERE.parent), str(HERE.parent / "spatail")):  # config/meshy_client; asset_manifest; object_size
    if p not in sys.path:
        sys.path.insert(0, p)

import config         # noqa: E402
import meshy_client   # noqa: E402
import asset_manifest as am  # noqa: E402  (studio/ — the Blender↔SPATAIL contract)
import object_size as osize   # noqa: E402  (studio/spatail — real-world scale grounding)

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


_SWING_WORDS = ("pendulum", "swing", "metronome", "wrecking ball")


def _motion_for(subject: str, desc: str) -> dict:
    """The animator's clip plan (deterministic v1: whole-object filler rigs).
    swing for pendulum-class subjects; the universal 'showcase' (slow turntable
    + breathing bob) for everything else — alive and inspectable from all sides.
    Per-part rigging on split meshes is the next stage of the animator."""
    text = f"{subject} {desc}".lower()
    if any(w in text for w in _SWING_WORDS):
        return {"kind": "swing", "amplitude_deg": 25.0}
    return {"kind": "showcase"}


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


VISION_DIR = REPO / "studio" / "vision"


def _vision_enabled() -> bool:
    return os.environ.get("SPATAIL_MESHY_VISION", "0").lower() in ("1", "true", "yes", "on")


def _vision_normalize(slug, subject, glb_in, real_size_m, asset_out):
    """Opt-in (SPATAIL_MESHY_VISION=1): replace the blind uniform-fit normalize with the
    vision-guided pipeline (studio/vision) — evidence -> Gemini review -> reorient +
    real-scale + repair + verify render. Returns {glb, usdz, review, applied} or None on
    any failure (caller then falls back to meshy_normalize, so this never breaks a build).
    Note: this is a PREP-time path; the default live server path leaves it OFF so no
    Gemini round-trip is added once a prompt is in flight."""
    try:
        out_root = Path(asset_out) / "vision"
        cmd = [sys.executable, str(VISION_DIR / "driver.py"), "all",
               "--asset", str(glb_in), "--subject", subject, "--gemini", "--out", str(out_root)]
        if real_size_m and max(real_size_m) > 0:
            cmd += ["--real-size", ",".join(str(round(float(x), 4)) for x in real_size_m)]
        # outer budget must exceed the inner evidence + review + apply passes combined
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=4200)
        od = out_root / slug
        ap = od / "reports" / "apply_report.json"
        glb = od / "exports" / "normalized.glb"
        if not (ap.exists() and glb.exists()):
            print(f"[asset] vision normalize incomplete; tail: "
                  f"{(proc.stderr or proc.stdout or '')[-600:]}")
            return None
        applied = json.loads(ap.read_text(encoding="utf-8"))
        rp = od / "reports" / "visual_review_result.json"
        rev = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {}
        # Accept ONLY a validated result from a real reviewer; a failed validation or a
        # neutral (reviewer="none") review falls back to the deterministic meshy_normalize.
        val_ok = bool((applied.get("validation") or {}).get("ok", False))
        reviewer = rev.get("reviewer", "")
        if not val_ok or reviewer in ("", "none"):
            print(f"[asset] vision normalize rejected (validation_ok={val_ok}, "
                  f"reviewer={reviewer!r}) — falling back to meshy_normalize")
            return None
        usdz = od / "exports" / "normalized.usdz"
        return {"glb": str(glb), "usdz": str(usdz) if usdz.exists() else "",
                "review": rev, "applied": applied.get("applied", {})}
    except Exception as exc:  # noqa: BLE001
        print(f"[asset] vision normalize failed ({exc}) — falling back to meshy_normalize")
        return None


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
    desc = _desc(subject, brief)
    scale_m = list(scale_meters or DEFAULT_SCALE_M)       # GLB authoring size (stable)
    # Correct scale detection (studio/spatail/object_size): the subject's REAL-WORLD
    # size, LLM-grounded + cached. Carried as METADATA for the placer/solver — it does
    # NOT resize the GLB, so the authoring size (and the anchor/animate stages that run
    # on it) stay robust; the placer rescales the mesh to this real size at placement.
    est = osize.estimate(subject, brief)
    real_size_m = est["size_m"] if est.get("source") != "fallback" else None
    if real_size_m:
        print(f"[asset] {slug}: real size {real_size_m} m ({est['source']}; {est.get('note', '')})")
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

    # 3) Normalize to real scale (metres/Y-up, library exporters) → LIB_DIR.
    # Opt-in (SPATAIL_MESHY_VISION=1): the vision-guided path SEES the mesh first —
    # evidence pack → review → reorient + real-scale + repair — instead of the blind
    # uniform-fit-to-0.4m that bakes in Meshy's arbitrary pose. Falls back on failure.
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    vision = _vision_normalize(slug, subject, glb_in, real_size_m, asset_out) \
        if _vision_enabled() else None
    if vision:
        on_stage("vision-validated normalize (orient + real scale)")
        shutil.copyfile(vision["glb"], LIB_DIR / f"{slug}.glb")
        if vision.get("usdz"):
            shutil.copyfile(vision["usdz"], LIB_DIR / f"{slug}.usdz")
        ap = vision.get("applied", {})
        print(f"[asset] {slug}: vision-normalized (class={ap.get('placement_class')}, "
              f"size={ap.get('final_size_m')}, rotated={ap.get('reorient', {}).get('rotated')})")
    else:
        on_stage("bringing it to real scale")
        norm = _run_blender(
            HERE / "meshy_normalize.py",
            {"result_path": str(OUT / f"_{slug}_normalize.json"),
             "assets": [{"assetId": slug, "category": category, "glb_in": str(glb_in),
                         "scaleMeters": scale_m, "pivot": "center_bottom",
                         "out_dir": str(LIB_DIR)}]},
            OUT / f"_{slug}_normalize.json")
        if not norm.get("done"):
            raise RuntimeError(f"normalize failed: {norm.get('failed')}")

    # 3.5) ANCHORS — Blender-computed semantic surface anchors (Phase 1 of the
    # spatial-intelligence plan): render canonical views of the normalized mesh,
    # Gemini points at the features, back-project onto the mesh (BVH), QA with
    # marker dots. Non-fatal: an asset without anchors still ships (the phone
    # falls back to its bbox heuristics, as today).
    anchors: list = []
    if os.environ.get("SPATAIL_MESHY_ANCHORS", "1").lower() not in ("0", "false", "no"):
        on_stage("locating the features (anchors)")
        try:
            anc = _run_blender(
                HERE / "anchors.py",
                {"result_path": str(OUT / f"_{slug}_anchors.json"),
                 "assets": [{"assetId": slug, "subject": subject, "desc": desc,
                             "glb_in": str(LIB_DIR / f"{slug}.glb"),
                             "out_dir": str(asset_out / "anchors")}]},
                OUT / f"_{slug}_anchors.json")
            if anc.get("failed"):
                raise RuntimeError(anc["failed"][0].get("error", "anchors stage failed"))
            anchors = am.normalize_anchors((anc.get("done") or [{}])[0].get("anchors"))
            print(f"[asset] {slug}: {len(anchors)} anchors "
                  f"({', '.join(x['name'] for x in anchors) or 'none'})")
        except Exception as exc:  # noqa: BLE001
            print(f"[asset] anchors skipped ({exc}) — shipping without anchors")

    # 4) Decimate to the mobile budget (~12K tris / 1K textures) → <slug>_ar.usdz
    ship_usdz = LIB_DIR / f"{slug}.usdz"
    ship_glb = LIB_DIR / f"{slug}.glb"
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
                if (LIB_DIR / f"{slug}_ar.glb").exists():
                    ship_glb = LIB_DIR / f"{slug}_ar.glb"
        except Exception as exc:  # noqa: BLE001
            print(f"[asset] decimate skipped ({exc}) — shipping full-res USDZ")
    if not ship_usdz.exists():
        raise RuntimeError("no USDZ produced")

    # 4.5) ANIMATE — the studio's "artist bakes the motion" stage: whole-object
    # filler-rig clips keyframed in Blender onto the optimized mesh, exported
    # WITH animation. Non-fatal: a static asset still ships if this fails.
    clips: list = []
    if os.environ.get("SPATAIL_MESHY_ANIMATE", "1").lower() not in ("0", "false", "no"):
        on_stage("animating (Blender)")
        try:
            motion = _motion_for(subject, desc)
            anim = _run_blender(
                HERE / "animate.py",
                {"result_path": str(OUT / f"_{slug}_animate.json"),
                 "assets": [{"assetId": slug, "glb_in": str(ship_glb),
                             "out_dir": str(LIB_DIR), "motion": motion,
                             "fps": 30, "seconds": 6.0}]},
                OUT / f"_{slug}_animate.json")
            d = (anim.get("done") or [{}])[0]
            a_usdz, a_glb = LIB_DIR / f"{slug}_anim.usdz", LIB_DIR / f"{slug}_anim.glb"
            if d.get("clips") and a_glb.exists():
                clips = d["clips"]
                ship_glb = a_glb
                if a_usdz.exists():
                    ship_usdz = a_usdz
                print(f"[asset] {slug}: animated ({clips[0]['name']}, {d.get('fcurves')} fcurves)")
        except Exception as exc:  # noqa: BLE001
            print(f"[asset] animate skipped ({exc}) — shipping static asset")

    # 5) Publish into the job's artifacts (existing phone polling streams it in).
    # USDZ feeds the phone; the GLB twin feeds the web viewer (model-viewer).
    on_stage("publishing")
    usdz_name = f"{job_id}.usdz"
    shutil.copyfile(ship_usdz, artifacts_dir / usdz_name)
    glb_name = ""
    if ship_glb.exists():
        glb_name = f"{job_id}.glb"
        shutil.copyfile(ship_glb, artifacts_dir / glb_name)
    # library URLs follow what we actually ship (animated > decimated > full-res)
    lib_glb = f"{LIB_URL}/{ship_glb.name}"
    lib_usdz = f"{LIB_URL}/{ship_usdz.name}"
    meta = {
        "source": "meshy", "subject": subject, "desc": desc, "assetId": slug,
        "scaleMeters": scale_m, "realSizeMeters": real_size_m, "credits": credits,
        "clips": clips, "anchors": anchors,
        "usdzBytes": (artifacts_dir / usdz_name).stat().st_size,
        "libraryGlb": lib_glb, "libraryUsdz": lib_usdz,
        "elapsedS": round(time.time() - t0, 1),
    }
    if vision:
        ap = vision.get("applied", {})
        q = (vision.get("review", {}).get("quality") or {})
        meta["vision"] = vision.get("review", {})
        meta["placementClass"] = ap.get("placement_class")
        meta["xrReady"] = bool(q.get("xr_ready", True))
        # The vision path BAKES real scale into the GLB itself (unlike the uniform-fit
        # path, where the GLB stays at authoring scale and realSizeMeters is metadata a
        # placer applies). realScaleBaked records WHICH contract this asset follows.
        # NOTE: no consumer reads it YET — the placer-wiring step (the "wire into Windows
        # placement" follow-up) MUST branch on it: realScaleBaked → render at scale 1.0
        # (already metric); else fit to realSizeMeters. The library registration below
        # also carries the baked size as scaleMeters so the two stay consistent.
        if ap.get("final_size_m"):
            meta["realSizeMeters"] = ap["final_size_m"]
            meta["realScaleBaked"] = True
    metadata_name = f"{job_id}_metadata.json"
    (artifacts_dir / metadata_name).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    # the Blender↔composer contract: clips the sequencer plays by name + anchors the
    # director pins steps to. Written under BOTH names: {job_id}_ (the phone's
    # manifest_url when polling this job) and {slug}_ (what the composer's
    # experience._load_manifest reads by ASSET id when composing the contract).
    if clips or anchors or real_size_m:
        manifest = json.dumps({"schema": am.SCHEMA, "assetId": slug, "parts": [],
                               "clips": clips, "anchors": anchors,
                               "realSizeMeters": real_size_m}, indent=2)
        (artifacts_dir / f"{job_id}_manifest.json").write_text(manifest, encoding="utf-8")
        (artifacts_dir / f"{slug}_manifest.json").write_text(manifest, encoding="utf-8")

    # 6) Library registration → the next prompt for this subject skips generation.
    # When vision baked real scale into the GLB, register that real size as scaleMeters
    # (the solver/footprint truth) instead of the [0.4,0.4,0.4] authoring guess.
    register_scale = scale_m
    if vision and vision.get("applied", {}).get("final_size_m"):
        register_scale = vision["applied"]["final_size_m"]
    _register(slug, subject, desc, register_scale, category, lib_glb, lib_usdz)

    print(f"[asset] {slug}: meshy asset ready in {meta['elapsedS']}s "
          f"({meta['usdzBytes'] / 1e6:.1f} MB, {credits} credits, "
          f"{'clip ' + clips[0]['name'] if clips else 'static'})")
    return {"usdz_name": usdz_name, "metadata_name": metadata_name,
            "glb_name": glb_name, "clips": clips, "anchors": anchors,
            "realSizeMeters": real_size_m,
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
