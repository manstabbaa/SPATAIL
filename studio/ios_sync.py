"""ios_sync.py - build every exhibit as AR USDZ and copy into the iOS app.

    python studio/ios_sync.py [--scene studio/scenes/newtons_laws.json]

For each beat in the scene it runs the single-exhibit AR+USDZ build, then copies
<exhibit>.usdz + <exhibit>_metadata.json into ios/SpatailEducator/Resources so
the app's Catalog picks them up. This is the Blender -> SPATAIL -> iOS bridge.
Run on Windows; the artifacts are committed and the Mac just compiles.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

STUDIO = Path(__file__).resolve().parent
ROOT = STUDIO.parent
RES = ROOT / "ios" / "SpatailEducator" / "Resources"
BLENDER = os.environ.get(
    "BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default=str(STUDIO / "scenes" / "newtons_laws.json"))
    a = ap.parse_args()
    scene = Path(a.scene)
    spec = json.loads(scene.read_text(encoding="utf-8"))
    out = STUDIO / "out"
    RES.mkdir(parents=True, exist_ok=True)
    if not Path(BLENDER).exists():
        sys.exit(f"[ios_sync] Blender not found at {BLENDER} (set BLENDER_EXE)")

    copied = []
    for beat in spec["beats"]:
        bid = beat["id"]
        print(f"[ios_sync] building {bid} …")
        r = subprocess.run(
            [BLENDER, "--background", "--factory-startup", "--python",
             str(STUDIO / "blender" / "build_studio.py"), "--",
             str(scene), str(out), "--exhibit", bid, "--ar", "--usdz"],
            capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-1500:]); print(r.stderr[-1500:])
            sys.exit(f"[ios_sync] build failed for {bid}")
        for ext in (".usdz", "_metadata.json"):
            src = out / f"studio_{bid}{ext}"
            if src.exists():
                dst = RES / src.name
                shutil.copy2(src, dst)
                copied.append(dst.name)
    print(f"[ios_sync] copied {len(copied)} files into {RES}:")
    for c in copied:
        print(f"           {c}")
    print("[ios_sync] done. On the Mac: cd ios/SpatailEducator && xcodegen && open in Xcode.")


if __name__ == "__main__":
    main()
