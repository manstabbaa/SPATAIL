"""run.py - the SPATAIL Studio pipeline.

    python studio/run.py [--brief studio/brief/newtons_laws.json]

Drives the studio handoff end to end:
  brief -> (Director: scene spec) -> Blender build (Artist + Developer geometry)
        -> contract (Developer handoff) -> ready for the viewer / XR runtime.

The Director step is a passthrough today (the scene spec is pre-authored); in
the live agent team the Director writes studio/scenes/<id>.json from the brief.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

STUDIO = Path(__file__).resolve().parent
BLENDER = os.environ.get(
    "BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")
PY = sys.executable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", default=str(STUDIO / "brief" / "newtons_laws.json"))
    args = ap.parse_args()

    brief = json.loads(Path(args.brief).read_text(encoding="utf-8"))
    scene_id = brief.get("scene") or brief.get("briefId")
    scene_spec = STUDIO / "scenes" / f"{scene_id}.json"
    if not scene_spec.exists():
        sys.exit(f"[studio] no scene spec at {scene_spec} (Director must author it)")
    out_dir = STUDIO / "out"
    out_dir.mkdir(exist_ok=True)
    print(f"[studio] brief={args.brief}")
    print(f"[studio] scene={scene_spec}")

    if not Path(BLENDER).exists():
        sys.exit(f"[studio] Blender not found at {BLENDER} (set BLENDER_EXE)")

    print("[studio] artist + developer: building in headless Blender ...")
    build = subprocess.run(
        [BLENDER, "--background", "--factory-startup", "--python",
         str(STUDIO / "blender" / "build_studio.py"), "--",
         str(scene_spec), str(out_dir)],
        capture_output=True, text=True)
    if build.returncode != 0:
        print(build.stdout[-2000:])
        print(build.stderr[-2000:])
        sys.exit("[studio] Blender build failed")
    for line in build.stdout.splitlines():
        if line.startswith("[build_studio]"):
            print("  " + line)

    print("[studio] developer handoff: emitting StudioSceneContract ...")
    contract_out = out_dir / "StudioSceneContract.json"
    cs = subprocess.run(
        [PY, str(STUDIO / "contract.py"), "--brief", args.brief,
         "--scene", str(scene_spec), "--metadata", str(out_dir / "studio_metadata.json"),
         "--out", str(contract_out)],
        capture_output=True, text=True)
    if cs.returncode != 0:
        print(cs.stdout)
        print(cs.stderr)
        sys.exit("[studio] contract failed")
    print(f"[studio] contract -> {contract_out}")
    print("[studio] done. View with:")
    print(f"        {PY} studio/viewer/server.py")
    print("        then open http://localhost:5180/studio/viewer/studio.html")


if __name__ == "__main__":
    main()
