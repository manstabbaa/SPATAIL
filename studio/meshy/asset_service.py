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
    SPATAIL_MESHY_VISION=1|0              force vision QA on/off; unset = auto (ON when
                                          the slug's review is cached — no Gemini cost)
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
for p in (str(HERE), str(HERE.parent), str(HERE.parent / "spatail"),
          str(HERE.parent / "director")):  # config/meshy_client; asset_manifest; object_size; asset_brief
    if p not in sys.path:
        sys.path.insert(0, p)

import config         # noqa: E402
import meshy_client   # noqa: E402
import asset_manifest as am  # noqa: E402  (studio/ — the Blender↔SPATAIL contract)
import object_size as osize   # noqa: E402  (studio/spatail — real-world scale grounding)
import asset_brief as abrief  # noqa: E402  (studio/director — the story→asset contract)

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "studio" / "out" / "meshy"
LIB_DIR = REPO / "public" / "assets" / "spatail-library" / "meshy"
LIB_URL = "/assets/spatail-library/meshy"
BLENDER_EXE = os.environ.get(
    "BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")

DEFAULT_SCALE_M = [0.4, 0.4, 0.4]
AR_TRIS = int(os.environ.get("SPATAIL_MESHY_TRIS", "12000"))
AR_TEX = int(os.environ.get("SPATAIL_MESHY_TEX", "1024"))

# Canonical ordered pipeline stages → a determinate build-progress bar (dev tool).
# on_stage() emits these labels in order; the server maps each to its index (job_server
# stamps stageIndex/stageTotal). Some stages are conditional (anchors/regions/remesh), so
# the bar advances monotonically and may skip — that's expected. Aliases map the
# branch-specific labels (vision normalize, showcase animate) onto the canonical step.
MESHY_STAGES = [
    "imagining the object (multi-view)",
    "sculpting the 3D model (Meshy)",
    "re-topologizing (quad remesh)",
    "bringing it to real scale",
    "locating the features (anchors)",
    "carving the parts (regions)",
    "optimizing for AR",
    "growing it (rig)",
    "publishing",
]
_STAGE_ALIASES = {
    "vision-validated normalize (orient + real scale)": "bringing it to real scale",
    "animating (Blender)": "growing it (rig)",
}


def stage_index(label: str) -> int:
    """1-based position of a stage label in MESHY_STAGES (0 if unknown). Monotonic."""
    label = _STAGE_ALIASES.get(label, label)
    try:
        return MESHY_STAGES.index(label) + 1
    except ValueError:
        return 0


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


def _vision_review_cache(slug: str) -> Path:
    # where driver.py "all" writes/reuses the review for _vision_normalize's out tree
    return OUT / slug / "vision" / slug / "reports" / "visual_review_result.json"


def _vision_enabled(slug: str) -> bool:
    """SPATAIL_MESHY_VISION=0 force-disables, =1 force-enables. Unset: vision QA
    defaults ON when a real review is already cached for the slug (the driver
    reuses it — no Gemini round-trip, so no latency cost on the live path) and
    stays opt-in for fresh subjects."""
    v = os.environ.get("SPATAIL_MESHY_VISION", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    rp = _vision_review_cache(slug)
    try:
        rev = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else None
    except Exception:  # noqa: BLE001
        return False
    return bool(rev) and rev.get("reviewer", "") not in ("", "none")


def _vision_normalize(slug, subject, glb_in, real_size_m, asset_out):
    """Gated by _vision_enabled(): replace the blind uniform-fit normalize with the
    vision-guided pipeline (studio/vision) — evidence -> Gemini review (reused when
    cached) -> reorient + real-scale + repair + verify render. Returns {glb, usdz,
    verify_render, apply_report, review, applied} or None on any failure (caller then
    falls back to meshy_normalize, so this never breaks a build). Fresh subjects stay
    opt-in (env) so no Gemini round-trip is added once a prompt is in flight."""
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
                "verify_render": str(od / "preview" / "verify.png"),
                "apply_report": str(ap),
                "review": rev, "applied": applied.get("applied", {})}
    except Exception as exc:  # noqa: BLE001
        print(f"[asset] vision normalize failed ({exc}) — falling back to meshy_normalize")
        return None


def _register(slug: str, name: str, desc: str, scale_m: list, category: str,
              glb_url: str, usdz_url: str, *,
              real_size_m: list | None = None, real_scale_baked: bool = False) -> None:
    """Best-effort library registration so the next prompt resolves this asset
    directly (no generation job at all). Failure here never fails the build.

    real_size_m/real_scale_baked carry the real-world scale contract into the library
    so the modular contract (and the iOS runtime) can render the asset at its true size
    instead of re-fitting every model to the tabletop footprint budget."""
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
            asset_state="generated",
            real_size_meters=real_size_m, real_scale_baked=real_scale_baked)
        print(f"[asset] registered {slug} in the library -> {usdz_url}")
    except Exception as exc:  # noqa: BLE001
        print(f"[asset] library registration skipped: {exc}")


def produce(subject: str, brief: str, job_id: str, artifacts_dir, *,
            asset_id: str | None = None, scale_meters: list | None = None,
            category: str = "general", story_requirements: dict | None = None,
            on_stage=lambda s: None) -> dict:
    """Full chain. Returns the SAME shape generator/headless_build return
    ({usdz_name, metadata_name, max_dim, spec}) so job_server's done-handling and
    the phone's polling work unchanged. Raises on failure → caller falls back.

    story_requirements (asset_brief): the STORY's per-asset demands the director
    emitted BEFORE generation — which named parts must be addressable (so a step can
    make exactly the lion's eye emissive). It biases the anchors stage to FIND those
    parts and drives the regions stage to bake them as precise overlays."""
    t0 = time.time()
    artifacts_dir = Path(artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(asset_id or subject)
    desc = _desc(subject, brief)
    # The story→asset contract (slice 1): which parts the lesson will point at.
    asset_brief = abrief.validate(story_requirements) if story_requirements else None
    if abrief.has_requirements(asset_brief):
        print(f"[asset] {slug}: story brief — addressable parts "
              f"{[p['id'] for p in abrief.addressable_parts(asset_brief)]}")
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
    info = None
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

    # 2.5) QUAD REMESH (Meshy API, 5 credits) — re-topologize the raw multi-image
    # tri-soup into ONE clean, lower-poly surface. This is the fix for the "chopped up /
    # gaps on device" problem: a connected surface deforms without splitting, and it's
    # riggable (weight-paintable). Quads survive only in FBX (GLB/USDZ triangulate), so
    # we fetch the FBX too for the armature rig. Cached per slug → re-runs cost 0.
    quad_fbx = asset_out / "meshy" / f"{slug}_quad.fbx"
    if os.environ.get("SPATAIL_MESHY_REMESH", "1").lower() not in ("0", "false", "no"):
        quad_glb = asset_out / "meshy" / f"{slug}_quad.glb"
        if not quad_glb.exists():
            on_stage("re-topologizing (quad remesh)")
            try:
                task_id = (info or {}).get("task_id")
                if not task_id:
                    rj = asset_out / "meshy" / "result.json"
                    task_id = json.loads(rj.read_text(encoding="utf-8")).get("task_id") \
                        if rj.exists() else None
                if not task_id:
                    raise RuntimeError("no Meshy task_id for remesh")
                polys = int(os.environ.get("SPATAIL_MESHY_REMESH_POLYS", "12000"))
                res = meshy_client.remesh(task_id, topology="quad", target_polycount=polys,
                                          target_formats=("glb", "fbx"))
                credits += res.get("consumed_credits") or 0
                urls = res.get("model_urls") or {}
                if urls.get("glb"):
                    meshy_client._download(urls["glb"], quad_glb)
                if urls.get("fbx"):
                    meshy_client._download(urls["fbx"], quad_fbx)
            except Exception as exc:  # noqa: BLE001
                print(f"[asset] remesh skipped ({exc}) — using raw Meshy mesh")
        else:
            print(f"[asset] {slug}: reusing cached quad remesh (0 credits)")
        if quad_glb.exists():
            glb_in = quad_glb       # the rest of the pipeline works on the clean mesh

    # 3) Normalize to real scale (metres/Y-up, library exporters) → LIB_DIR.
    # Vision QA (auto on cached reviews, env-forced otherwise): the vision-guided path
    # SEES the mesh first — evidence pack → review → reorient + real-scale + repair —
    # instead of the blind uniform-fit-to-0.4m that bakes in Meshy's arbitrary pose.
    # Falls back on failure.
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    norm_qa = None
    vision = _vision_normalize(slug, subject, glb_in, real_size_m, asset_out) \
        if _vision_enabled(slug) else None
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
        norm_qa = (norm.get("done") or [{}])[0]

    # 3.5) ANCHORS — Blender-computed semantic surface anchors (Phase 1 of the
    # spatial-intelligence plan): render canonical views of the normalized mesh,
    # Gemini points at the features, back-project onto the mesh (BVH), QA with
    # marker dots. Non-fatal: an asset without anchors still ships (the phone
    # falls back to its bbox heuristics, as today).
    anchors: list = []
    raw_anchors: list = []           # carries pos_world (Blender Z-up) for the regions stage
    if os.environ.get("SPATAIL_MESHY_ANCHORS", "1").lower() not in ("0", "false", "no"):
        on_stage("locating the features (anchors)")
        try:
            anc = _run_blender(
                HERE / "anchors.py",
                {"result_path": str(OUT / f"_{slug}_anchors.json"),
                 "assets": [{"assetId": slug, "subject": subject, "desc": desc,
                             "glb_in": str(LIB_DIR / f"{slug}.glb"),
                             # story-driven bias: find the parts the lesson will explain
                             "required_names": abrief.required_part_names(asset_brief)
                             if asset_brief else [],
                             "out_dir": str(asset_out / "anchors")}]},
                OUT / f"_{slug}_anchors.json")
            if anc.get("failed"):
                raise RuntimeError(anc["failed"][0].get("error", "anchors stage failed"))
            raw_anchors = (anc.get("done") or [{}])[0].get("anchors") or []
            anchors = am.normalize_anchors(raw_anchors)
            print(f"[asset] {slug}: {len(anchors)} anchors "
                  f"({', '.join(x['name'] for x in anchors) or 'none'})")
        except Exception as exc:  # noqa: BLE001
            print(f"[asset] anchors skipped ({exc}) — shipping without anchors")

    # 3.6) REGIONS — story-driven addressable sub-mesh overlays (slice 1). For each
    # part the director declared addressable that the anchors stage localized, bake a
    # precise overlay (`spatail_region__<mesh>__<id>`) and re-export the GLB so the
    # downstream decimate/animate stages carry the overlays into the shipped USDZ.
    # Non-fatal: the asset still ships (steps fall back to anchor/bbox) if this fails.
    regions: list = []
    want_parts = abrief.addressable_parts(asset_brief) if asset_brief else []
    if want_parts and raw_anchors:
        on_stage("carving the parts (regions)")
        try:
            by_name = {a.get("name"): a for a in raw_anchors if a.get("name")}
            specs = []
            for p in want_parts:
                anc_hit = next((by_name[n] for n in
                                [p["id"], p.get("role")] + list(p.get("aliases") or [])
                                if n in by_name), None)
                if not anc_hit:
                    continue
                specs.append({
                    "id": p["id"], "label": p["id"].replace("_", " ").title(),
                    "role": p.get("role") or p["id"],
                    "effects": p.get("effects") or ["highlight"],
                    "pos_world": anc_hit.get("pos_world"), "pos_m": anc_hit.get("pos_m"),
                    "offset": anc_hit.get("offset"), "verified": anc_hit.get("verified", False),
                })
            if specs:
                reg = _run_blender(
                    HERE / "regions.py",
                    {"result_path": str(OUT / f"_{slug}_regions.json"),
                     "export_usdz": True,
                     "assets": [{"assetId": slug, "glb_in": str(LIB_DIR / f"{slug}.glb"),
                                 "out_dir": str(LIB_DIR), "regions": specs}]},
                    OUT / f"_{slug}_regions.json")
                if reg.get("failed"):
                    raise RuntimeError(reg["failed"][0].get("error", "regions stage failed"))
                regions = am.normalize_regions((reg.get("done") or [{}])[0].get("regions"))
                print(f"[asset] {slug}: {len(regions)} regions "
                      f"({', '.join(x['id'] for x in regions) or 'none'})")
        except Exception as exc:  # noqa: BLE001
            print(f"[asset] regions skipped ({exc}) — shipping without regions")

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

    # 4.4) GROW — STORY-driven growth rig (slice 2). When the brief declares an animation
    # of kind "grow", the asset GROWS instead of doing the generic showcase spin. Two
    # backends (brief `rig`): "armature" (DEFAULT — a weight-painted bone chain deforms the
    # single connected mesh; no cutting, no gaps) or "morph" (geometry-nodes sparse
    # shape-key bake, lighter). Runs on the optimized, remeshed ship mesh.
    clips: list = []
    grew = False
    anim_spec = abrief.animation_spec(asset_brief) if asset_brief else None
    if anim_spec and anim_spec.get("kind") == "grow":
        rig = anim_spec.get("rig", "armature")
        on_stage("growing it (rig)")
        try:
            common = {"assetId": slug, "glb_in": str(ship_glb), "out_dir": str(LIB_DIR),
                      "fps": int(anim_spec.get("fps", 30)),
                      "seconds": float(anim_spec.get("seconds", 4.0)),
                      "static_floor": float(anim_spec.get("static_floor", 0.0))}
            if rig == "armature":
                script = HERE / "grow_armature.py"
                spec_asset = {**common, "n_bones": int(anim_spec.get("n_bones", 6))}
            else:
                script = HERE / "grow.py"
                spec_asset = {**common, "n_keys": int(anim_spec.get("n_keys", 8))}
            gres = _run_blender(
                script,
                {"result_path": str(OUT / f"_{slug}_grow.json"), "export_usdz": True,
                 "assets": [spec_asset]},
                OUT / f"_{slug}_grow.json")
            if gres.get("failed"):
                raise RuntimeError(gres["failed"][0].get("error", "grow stage failed"))
            gd = (gres.get("done") or [{}])[0]
            g_glb = Path(gd.get("glb") or "")
            if gd.get("clips") and g_glb.exists():
                clips = gd["clips"]
                ship_glb = g_glb
                g_usdz = Path(gd.get("usdz") or "")
                if g_usdz.exists():
                    ship_usdz = g_usdz
                grew = True
                print(f"[asset] {slug}: grown via {rig} "
                      f"({gd.get('bones') or gd.get('n_keys')} {'bones' if rig == 'armature' else 'keys'})")
        except Exception as exc:  # noqa: BLE001
            print(f"[asset] grow skipped ({exc}) — falling back to showcase animate")

    # 4.5) ANIMATE — the fallback "artist bakes the motion" stage: whole-object filler
    # clips (showcase spin) keyframed onto the optimized mesh. SKIPPED when the asset
    # already grew. Non-fatal: a static asset still ships if this fails.
    if not grew and os.environ.get("SPATAIL_MESHY_ANIMATE", "1").lower() not in ("0", "false", "no"):
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
        "clips": clips, "anchors": anchors, "regions": regions,
        "usdzBytes": (artifacts_dir / usdz_name).stat().st_size,
        "libraryGlb": lib_glb, "libraryUsdz": lib_usdz,
        "elapsedS": round(time.time() - t0, 1),
    }
    # QA provenance: ALWAYS reference the normalize QA image + report, whichever
    # normalize path ran, so every job's metadata points at visual proof.
    if vision:
        meta["normalizeQA"] = {
            "pipeline": "vision",
            "image": vision.get("verify_render", ""),
            "report": vision.get("applied", {}),
            "reportFile": vision.get("apply_report", ""),
        }
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
    else:
        meta["normalizeQA"] = {
            "pipeline": "meshy_normalize",
            "image": (norm_qa or {}).get("qa_render", ""),
            "report": (norm_qa or {}).get("qa") or {},
            "reportFile": str(OUT / f"_{slug}_normalize.json"),
        }
    metadata_name = f"{job_id}_metadata.json"
    (artifacts_dir / metadata_name).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    # the Blender↔composer contract: clips the sequencer plays by name + anchors the
    # director pins steps to. Written under BOTH names: {job_id}_ (the phone's
    # manifest_url when polling this job) and {slug}_ (what the composer's
    # experience._load_manifest reads by ASSET id when composing the contract).
    if clips or anchors or regions or real_size_m:
        manifest = json.dumps({"schema": am.SCHEMA, "assetId": slug, "parts": [],
                               "clips": clips, "anchors": anchors, "regions": regions,
                               "realSizeMeters": real_size_m}, indent=2)
        (artifacts_dir / f"{job_id}_manifest.json").write_text(manifest, encoding="utf-8")
        (artifacts_dir / f"{slug}_manifest.json").write_text(manifest, encoding="utf-8")

    # 6) Library registration → the next prompt for this subject skips generation.
    # Carry the real-world scale contract through so the runtime renders at true size:
    #  - vision path BAKED real scale into the GLB → realScaleBaked, and the baked size
    #    doubles as scaleMeters (solver/footprint truth) instead of the authoring guess.
    #  - uniform-fit path leaves the GLB at authoring scale but knows the real size
    #    (object_size estimate) → realSizeMeters tells the runtime what to fit it to.
    register_scale = scale_m
    real_baked = bool(vision and vision.get("applied", {}).get("final_size_m"))
    if real_baked:
        register_scale = vision["applied"]["final_size_m"]
    real_size_contract = vision["applied"]["final_size_m"] if real_baked else real_size_m
    _register(slug, subject, desc, register_scale, category, lib_glb, lib_usdz,
              real_size_m=real_size_contract, real_scale_baked=real_baked)

    print(f"[asset] {slug}: meshy asset ready in {meta['elapsedS']}s "
          f"({meta['usdzBytes'] / 1e6:.1f} MB, {credits} credits, "
          f"{'clip ' + clips[0]['name'] if clips else 'static'})")
    return {"usdz_name": usdz_name, "metadata_name": metadata_name,
            "glb_name": glb_name, "clips": clips, "anchors": anchors, "regions": regions,
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
