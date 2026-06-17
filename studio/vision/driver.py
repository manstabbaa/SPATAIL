"""driver.py — the bpy-free orchestrator for the vision-guided asset pipeline.

This is the entry point that PULLS IN MESHY CONTENT and works from there. It resolves
an asset to a source GLB (cached Meshy download, an explicit path, or a FRESH Meshy
generation), lays out the output tree, and spawns the headless-Blender passes around
the visual review.

    # 1) build the evidence pack from a cached Meshy asset (the reviewer's input)
    python studio/vision/driver.py evidence --asset frog

    # 2a) review with Claude  -> the /spatail-asset-vision skill drives this:
    #     it reads asset_output/<id>/preview/contact_sheet.png + reports/asset_report.json
    #     and writes asset_output/<id>/reports/visual_review_result.json
    # 2b) OR review headless with Gemini (no human in the loop):
    python studio/vision/driver.py review --asset frog --gemini

    # 3) apply the review: reorient + real-scale + repair + export + verify render
    python studio/vision/driver.py apply --asset frog

    # headless end-to-end (evidence -> Gemini review -> apply) in one shot:
    python studio/vision/driver.py all --asset frog --gemini

    # pull a FRESH asset from Meshy first (spends credits; needs the API keys):
    python studio/vision/driver.py all --asset "soccer ball" --generate --gemini

The Blender passes (evidence.py / apply.py) make NO semantic decision; the review
step does. Everything keys on <out_root>/<slug>/ so re-runs are cheap and inspectable.
This runs at PREP time and bakes the corrected transform into the exported asset, so
the prompt-time placer pays zero extra latency.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (str(HERE), str(HERE.parent / "meshy"), str(HERE.parent / "spatail")):
    if p not in sys.path:
        sys.path.insert(0, p)

import vision_report as vr  # noqa: E402

MESHY_OUT = REPO / "studio" / "out" / "meshy"
BLENDER_EXE = os.environ.get(
    "BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", (s or "asset").lower()).strip("_")[:48] or "asset"


# ── source resolution: pull in Meshy content ────────────────────────────────────

def resolve_source(asset: str, *, generate: bool, subject: str | None) -> tuple[str, str, str]:
    """Return (slug, subject, source_glb_path). asset may be a slug, a path, or a
    subject to generate. Prefers an existing GLB; only generates when asked."""
    p = Path(asset)
    if p.suffix.lower() in (".glb", ".gltf") and p.exists():
        slug = _slug(p.stem)
        return slug, (subject or slug.replace("_", " ")), str(p)

    slug = _slug(asset)
    cached = MESHY_OUT / slug / "meshy" / f"{slug}.glb"
    if cached.exists():
        return slug, (subject or asset), str(cached)

    if not generate:
        raise SystemExit(
            f"no source GLB for {asset!r} (looked at {cached}). "
            f"Pass --generate to create it via Meshy, or pass a .glb path.")
    return _generate_meshy(slug, subject or asset)


def _generate_meshy(slug: str, subject: str) -> tuple[str, str, str]:
    """Steps 1-2 of the Meshy artist path (reused): Gemini multi-view -> Meshy 3D.
    Cached per slug, so a repeat costs 0 credits."""
    import config as mcfg  # studio/meshy/config
    if not (mcfg.get("MESHY_API_KEY") and mcfg.get("GEMINI_API_KEY")):
        raise SystemExit("Meshy/Gemini keys missing (~/.spatail/secrets.env) — cannot --generate")
    asset_out = MESHY_OUT / slug
    asset_out.mkdir(parents=True, exist_ok=True)
    glb = asset_out / "meshy" / f"{slug}.glb"
    if glb.exists():
        print(f"[driver] reusing cached Meshy GLB for {slug} (0 credits)")
        return slug, subject, str(glb)
    import gemini_images
    import meshy_client
    views = sorted(asset_out.glob("view_*.png"))
    if len(views) < 2:
        print(f"[driver] generating Gemini multi-view for {subject!r} ...")
        gemini_images.multiview({"id": slug, "desc": f"a {subject}"})
        views = sorted(asset_out.glob("view_*.png"))
    if not views:
        raise SystemExit("no reference views generated")
    print(f"[driver] Meshy multi-image-to-3D for {subject!r} (this spends credits) ...")
    info = meshy_client.generate_3d([str(v) for v in views], slug, target_polycount=30000)
    if info.get("status") != "SUCCEEDED" or "glb" not in info.get("files", {}):
        raise SystemExit(f"Meshy failed: {info.get('status')} {info.get('error')}")
    return slug, subject, info["files"]["glb"]["path"]


# ── output tree ──────────────────────────────────────────────────────────────

def out_tree(out_root: Path, slug: str) -> Path:
    d = Path(out_root) / slug
    for sub in ("imported", "preview", "reports", "exports", "logs"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


# ── Blender pass runner ──────────────────────────────────────────────────────

def _run_blender(script: str, spec: dict, result_path: Path, timeout=1800) -> dict:
    if not os.path.exists(BLENDER_EXE):
        raise SystemExit(f"Blender not found at {BLENDER_EXE!r} — set $BLENDER_EXE or install it")
    if result_path.exists():
        result_path.unlink()
    fd, sp = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(spec, f)
    try:
        proc = subprocess.run([BLENDER_EXE, "--background", "--python",
                               str(HERE / script), "--", sp],
                              capture_output=True, text=True, timeout=timeout)
        if not result_path.exists():
            tail = (proc.stderr or proc.stdout or "")[-1600:]
            raise RuntimeError(f"{script} produced no result; tail:\n{tail}")
    except FileNotFoundError as e:
        raise RuntimeError(f"could not launch Blender at {BLENDER_EXE!r}: {e}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{script} timed out after {timeout}s (heavy mesh? raise --res down or timeout)")
    finally:
        try:
            os.remove(sp)
        except OSError:
            pass
    return json.loads(result_path.read_text(encoding="utf-8"))


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_evidence(slug, subject, source, out_dir, res) -> dict:
    res_path = out_dir / "reports" / "_evidence_result.json"
    r = _run_blender("evidence.py", {
        "result_path": str(res_path), "assetId": slug, "subject": subject,
        "glb_in": source, "out_dir": str(out_dir), "render_res": res}, res_path)
    if not r.get("ok"):
        raise SystemExit(f"evidence failed: {r.get('error')}\n{r.get('trace_tail','')}")
    print(f"[driver] evidence ready: {out_dir/'preview'/'contact_sheet.png'}")
    print(f"[driver] REVIEW NEXT — read the contact sheet + {out_dir/'reports'/'asset_report.json'},\n"
          f"          write {out_dir/'reports'/'visual_review_result.json'} (schema {vr.REVIEW_SCHEMA}).")
    return r


def cmd_review_gemini(slug, subject, out_dir) -> dict:
    import review_gemini
    report = json.loads((out_dir / "reports" / "asset_report.json").read_text(encoding="utf-8"))
    review = review_gemini.review(out_dir, report, subject)
    rp = out_dir / "reports" / "visual_review_result.json"
    rp.write_text(json.dumps(review, indent=2), encoding="utf-8")
    if review.get("reviewer") in ("none", "") or review["orientation"]["confidence"] == 0.0:
        print("[driver] WARNING: gemini review is NEUTRAL (the call failed) — apply will FLAG "
              "this asset as not-reviewed (validation fails). Re-run, or use the Claude skill.")
    print(f"[driver] gemini review -> {rp}  "
          f"(up={review['orientation']['up_axis']} front={review['orientation']['front_axis']} "
          f"class={review['scale']['placement_class']})")
    return review


def cmd_apply(slug, subject, source, out_dir, res, real_size) -> dict:
    rp = out_dir / "reports" / "visual_review_result.json"
    if not rp.exists():
        raise SystemExit(f"no review at {rp} — run 'review --gemini' or the /spatail-asset-vision skill first")
    review = json.loads(rp.read_text(encoding="utf-8"))
    if real_size is None:
        real_size = _object_size(subject)
    res_path = out_dir / "reports" / "_apply_result.json"
    r = _run_blender("apply.py", {
        "result_path": str(res_path), "assetId": slug, "subject": subject,
        "glb_in": source, "out_dir": str(out_dir), "render_res": res,
        "review": review, "reviewer": review.get("reviewer", "external"),
        "realSizeMeters": real_size}, res_path)
    if not r.get("ok"):
        print(f"[driver] apply VALIDATION ISSUES: {r.get('errors')}")
    print(f"[driver] applied -> {r.get('glb')}\n"
          f"          verify: {r.get('verify_render')}  final {r.get('final_size_m')} m "
          f"class={r.get('placement_class')}")
    return r


def _object_size(subject: str):
    try:
        import object_size as osize
        est = osize.estimate(subject)
        if est.get("source") != "fallback":
            print(f"[driver] object_size: {subject!r} -> {est['size_m']} m ({est['source']})")
            return est["size_m"]
    except Exception as e:  # noqa: BLE001
        print(f"[driver] object_size unavailable ({e})")
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="SPATAIL vision-guided asset pipeline")
    ap.add_argument("command", choices=["evidence", "review", "apply", "all"])
    ap.add_argument("--asset", required=True, help="slug, .glb path, or subject (+--generate)")
    ap.add_argument("--subject", default=None, help="real-world subject (for scale/identity)")
    ap.add_argument("--out", default=str(REPO / "asset_output"), help="output root")
    ap.add_argument("--generate", action="store_true", help="generate via Meshy if no GLB exists")
    ap.add_argument("--gemini", action="store_true", help="review with Gemini (headless)")
    ap.add_argument("--force-review", action="store_true",
                    help="re-run the Gemini review even if visual_review_result.json exists")
    ap.add_argument("--real-size", default=None, help="w,h,d metres (else object_size LLM)")
    ap.add_argument("--res", type=int, default=768)
    args = ap.parse_args(argv)

    slug, subject, source = resolve_source(args.asset, generate=args.generate, subject=args.subject)
    out_dir = out_tree(Path(args.out), slug)
    real_size = None
    if args.real_size:
        try:
            real_size = [float(x) for x in args.real_size.split(",")][:3]
        except ValueError:
            raise SystemExit("--real-size must be 'w,h,d' in metres")
    print(f"[driver] asset={slug} subject={subject!r} source={source}\n[driver] out={out_dir}")
    t0 = time.time()

    if args.command == "evidence":
        cmd_evidence(slug, subject, source, out_dir, args.res)
    elif args.command == "review":
        if not args.gemini:
            raise SystemExit("'review' is headless-only (Gemini). Use --gemini, or run the "
                             "/spatail-asset-vision skill for the Claude-in-loop review.")
        cmd_review_gemini(slug, subject, out_dir)
    elif args.command == "apply":
        cmd_apply(slug, subject, source, out_dir, args.res, real_size)
    elif args.command == "all":
        cmd_evidence(slug, subject, source, out_dir, args.res)
        rp = out_dir / "reports" / "visual_review_result.json"
        if args.gemini:
            if rp.exists() and not args.force_review:
                print(f"[driver] reusing existing review {rp} (--force-review to re-run, saves a Gemini call)")
            else:
                cmd_review_gemini(slug, subject, out_dir)
        elif not rp.exists():
            raise SystemExit("'all' without --gemini needs a Claude review in the middle — "
                             "use the /spatail-asset-vision skill, or pass --gemini.")
        cmd_apply(slug, subject, source, out_dir, args.res, real_size)
    print(f"[driver] done in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
