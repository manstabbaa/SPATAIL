"""publish_to_spatail.py — bring a normalized factory asset into the SPATAIL app.

The asset factory produces a fixed-bounds GLB. iOS can't render GLB, so to TEST a
processed asset in the SpatailEducator app (no Xcode rebuild needed) we:

  1. ensure the asset has a RealityKit USDZ (rebuild with --export-usdz if absent),
  2. copy GLB+USDZ into public/assets/spatail-library/<category>/ (what the
     job_server serves over Tailscale), and
  3. register it into the SPATAIL library (manifests/generated.json) under a
     SUBJECT, so the Representation Engine resolves it as a real `library` hit and
     the app's RepresentationRuntime downloads + renders the USDZ.

This mirrors studio/meshy/present.py (which stages Meshy assets the same way) and
reuses studio/library/AssetLibrary.register_generated — the proven feedback path.

    python asset_factory/publish_to_spatail.py --asset-id coffee_mug --subject "a coffee mug"
    python asset_factory/publish_to_spatail.py --asset-id chair --subject "a wooden chair" --category furniture

Then on the phone: open the app -> "Generate something new" -> set Server to the
printed Tailscale URL -> mode "Representation" -> type the subject -> Represent.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import config  # noqa: E402  (bpy-free)

# AssetLibrary lives under studio/ — add it to the path the same way studio scripts do.
sys.path.insert(0, str(config.REPO_ROOT / "studio"))
from library.asset_library import AssetLibrary  # noqa: E402

WORKER = HERE / "process_one_asset.py"
LIB_URL = "/assets/spatail-library"               # job_server serves this from public/
LIB_DIR = config.REPO_ROOT / "public" / "assets" / "spatail-library"
SERVER_PORT = int(os.environ.get("SPATAIL_PORT", "8787"))
_STOP = {"a", "an", "the", "of", "my", "on", "in", "to", "and", "with", "for", "this", "that"}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "asset"


def _subject_tags(subject: str) -> list[str]:
    toks = [t for t in re.split(r"[^a-z0-9]+", (subject or "").lower())
            if len(t) > 1 and t not in _STOP]
    # de-dup, keep order
    seen, out = set(), []
    for t in toks:
        if t not in seen:
            seen.add(t); out.append(t)
    return out


def tailscale_base_url() -> str:
    """Best-effort http://<this-pc>.<tailnet>.ts.net:<port> for the phone to use."""
    try:
        r = subprocess.run(["tailscale", "status", "--json"], capture_output=True,
                           text=True, timeout=8)
        if r.returncode == 0:
            self_ = json.loads(r.stdout).get("Self", {})
            dns = (self_.get("DNSName") or "").rstrip(".")
            if dns:
                return f"http://{dns}:{SERVER_PORT}"
    except Exception:
        pass
    try:
        r = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=8)
        ip = (r.stdout or "").strip().splitlines()[0] if r.returncode == 0 else ""
        if ip:
            return f"http://{ip}:{SERVER_PORT}"
    except Exception:
        pass
    return f"http://<your-pc>.<tailnet>.ts.net:{SERVER_PORT}"


def _ensure_usdz(asset_id, input_dir, processed_dir, blender_exe, rebuild) -> Path:
    """Return the path to the asset's normalized USDZ, building it if necessary."""
    out_dir = Path(processed_dir) / asset_id
    usdz = out_dir / f"{asset_id}.normalized.usdz"
    if usdz.exists() and not rebuild:
        return usdz

    group_src = Path(input_dir) / asset_id
    if not group_src.exists():
        raise SystemExit(f"[publish] no USDZ at {usdz} and no raw source at {group_src} "
                         f"to rebuild from. Process the asset first, or pass --input.")
    if not os.path.exists(blender_exe):
        raise SystemExit(f"[publish] Blender not found at {blender_exe!r} (set --blender / $BLENDER_EXE).")

    print(f"[publish] building USDZ for {asset_id} (one Blender pass)…")
    timeout_s = int(config.load_worker_config().get("asset_timeout_seconds", 600))
    cmd = [blender_exe, "--background", "--python", str(WORKER), "--",
           "--asset-id", asset_id, "--input", str(input_dir), "--output", str(processed_dir),
           "--export-usdz"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    if not usdz.exists():
        tail = (proc.stderr or proc.stdout or "")[-1500:]
        raise SystemExit(f"[publish] USDZ build failed for {asset_id}.\n{tail}")
    return usdz


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Publish a normalized factory asset into the SPATAIL app")
    ap.add_argument("--asset-id", required=True)
    ap.add_argument("--subject", required=True,
                    help='what to type in the app, e.g. "a coffee mug"')
    ap.add_argument("--category", default="ingested",
                    help="library category folder (default: ingested)")
    ap.add_argument("--input", default="assets_raw")
    ap.add_argument("--processed", default="assets_processed")
    ap.add_argument("--tags", nargs="*", default=None, help="extra semantic tags")
    ap.add_argument("--blender", default=None)
    ap.add_argument("--rebuild", action="store_true", help="re-run the factory even if outputs exist")
    args = ap.parse_args(argv)

    asset_id = args.asset_id
    blender_exe = config.resolve_blender_exe(args.blender)
    processed_dir = Path(args.processed).resolve()

    usdz = _ensure_usdz(asset_id, Path(args.input).resolve(), processed_dir, blender_exe, args.rebuild)
    out_dir = processed_dir / asset_id
    glb = out_dir / f"{asset_id}.normalized.glb"
    manifest_path = out_dir / "asset_manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"[publish] missing manifest {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "success":
        raise SystemExit(f"[publish] asset {asset_id} did not process successfully "
                         f"(status={manifest.get('status')}).")

    fb = manifest.get("final_bounds_m", {})
    scale = [fb.get("x", 0.3), fb.get("y", 0.3), fb.get("z", 0.3)]
    pivot = "center_bottom" if manifest.get("origin_mode") == "bottom_center" else "center"

    # 2) copy GLB + USDZ into the served library folder
    slug = _slug(asset_id)
    cat = _slug(args.category)
    dst_dir = LIB_DIR / cat
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(usdz, dst_dir / f"{slug}.usdz")
    if glb.exists():
        shutil.copy(glb, dst_dir / f"{slug}.glb")
    web_glb = f"{LIB_URL}/{cat}/{slug}.glb"
    web_usdz = f"{LIB_URL}/{cat}/{slug}.usdz"

    # 3) register into the SPATAIL library so the engine resolves it as a library hit
    tags = _subject_tags(args.subject) + [slug] + list(args.tags or [])
    AssetLibrary().register_generated(
        slug, web_glb, category=cat, name=args.subject.strip(),
        semantic_tags=tags, scale_meters=scale, pivot=pivot,
        usdz_path=web_usdz, bbox_m={"size": scale}, asset_state="generated")

    # verify: a fresh library instance must resolve the subject to THIS asset's USDZ
    res = AssetLibrary().resolve(asset_id=slug, subject=args.subject)
    served = (dst_dir / f"{slug}.usdz").exists()
    ok = res.source == "library" and res.usdzPath == web_usdz and served

    base = tailscale_base_url()
    print("\n" + "=" * 64)
    print(f"PUBLISHED {asset_id}  ->  SPATAIL library asset '{slug}'")
    print(f"  category   : {cat}")
    print(f"  subject    : {args.subject!r}   tags={tags}")
    print(f"  final size : {scale} m   pivot={pivot}")
    print(f"  usdz (web) : {web_usdz}")
    print(f"  resolve()  : source={res.source} usdz={res.usdzPath!r}")
    print(f"  served file: {'yes' if served else 'NO'}")
    print(f"  verified   : {'OK — the app will render this asset' if ok else 'FAILED (see above)'}")
    print("=" * 64)
    print("\nTo see it on your phone:")
    print(f"  1. On the PC, start the server:   python studio/server/job_server.py")
    print(f"  2. In the app: 'Generate something new' -> open 'Server' and set:")
    print(f"         {base}")
    print(f"  3. Mode = 'Representation', type:  {args.subject}")
    print(f"  4. Tap 'Represent'. The normalized asset loads in AR on your table.")
    if not ok:
        print("\n[publish] WARNING: verification failed — fix before testing on the phone.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
