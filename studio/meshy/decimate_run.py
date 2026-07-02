"""decimate_run.py — orchestrate the AR decimation of the Meshy assets.

    python studio/meshy/decimate_run.py [--tris 12000] [--tex 1024] [--only <id>...]

Writes <id>_ar.glb / <id>_ar.usdz next to the full-res originals in
public/assets/spatail-library/meshy/ and prints the before/after USDZ sizes — the
numbers that decide whether image-to-3D is shippable to mobile AR.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import segment  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
MESHY = REPO / "public" / "assets" / "spatail-library" / "meshy"
BLENDER_EXE = os.environ.get(
    "BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")


def _arg(flag, default):
    a = sys.argv
    return type(default)(a[a.index(flag) + 1]) if flag in a else default


def main() -> int:
    tris = _arg("--tris", 12000)
    tex = _arg("--tex", 1024)
    only = set(sys.argv[sys.argv.index("--only") + 1:]) if "--only" in sys.argv else None
    assets = [a for a in segment.ASSETS if (not only or a["id"] in only)]
    spec_assets = [{"assetId": a["id"], "glb_in": str(MESHY / f"{a['id']}.glb"),
                    "out_dir": str(MESHY)}
                   for a in assets if (MESHY / f"{a['id']}.glb").exists()]
    if not spec_assets:
        print("no Meshy GLBs found — run pipeline.py first"); return 1
    res = REPO / "studio" / "out" / "meshy" / "_decimate_result.json"
    fd, sp = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"result_path": str(res), "target_tris": tris, "texture_max": tex,
                   "assets": spec_assets}, f)
    try:
        proc = subprocess.run(
            [BLENDER_EXE, "--background", "--python", str(HERE / "decimate.py"), "--", sp],
            capture_output=True, text=True, timeout=1800)
        if not res.exists():
            print("decimate produced no result; tail:\n", (proc.stderr or proc.stdout)[-1500:])
            return 1
    finally:
        try: os.remove(sp)
        except OSError: pass
    r = json.loads(res.read_text(encoding="utf-8"))
    print(f"\n{'asset':<18}{'tris':<20}{'orig USDZ':<12}{'AR USDZ':<11}{'reduction'}")
    print("-" * 70)
    for d in r["done"]:
        aid = d["assetId"]
        o = MESHY / f"{aid}.usdz"; n = MESHY / f"{aid}_ar.usdz"
        os_ = o.stat().st_size / 1e6 if o.exists() else 0.0
        ns = n.stat().st_size / 1e6 if n.exists() else 0.0
        red = f"{(1 - ns / os_) * 100:.0f}% smaller" if os_ else "n/a"
        print(f"{aid:<18}{f'{d['tris_before']}->{d['tris_after']}':<20}"
              f"{os_:>7.1f}MB   {ns:>6.1f}MB   {red}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
