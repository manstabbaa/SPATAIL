"""place_smoke.py — smoke-test the spatial placer on any subject + library asset.

    python studio/blender/place_smoke.py "<subject>" [asset_slug] [--clutter] [--floor]

What it does:
  * estimates the subject's REAL-WORLD size (studio/spatail/object_size, LLM+cache)
  * builds a representative room (RoomModel.demo: a table + a chair on the floor),
    optionally adding a mug ON the table (--clutter) or dropping the table (--floor)
  * runs studio/blender/spatial_placer.py headless and prints the placement + the
    path to a preview PNG you can open

Examples:
    python studio/blender/place_smoke.py "fire extinguisher" fire_extinguisher
    python studio/blender/place_smoke.py frog frog --clutter
    python studio/blender/place_smoke.py "pendulum clock" pendulum_clock --floor

Available library slugs: ls public/assets/spatail-library/meshy/*.glb
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "studio" / "spatail"))
import room_model      # noqa: E402
import object_size     # noqa: E402

BLENDER = os.environ.get(
    "BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")


def main():
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    subject = pos[0] if pos else "frog"
    slug = pos[1] if len(pos) > 1 else "frog"
    glb = REPO / "public" / "assets" / "spatail-library" / "meshy" / f"{slug}.glb"
    if not glb.exists():
        raise SystemExit(f"no library GLB at {glb}\n"
                         f"pick a slug from: {[p.stem for p in glb.parent.glob('*.glb')]}")

    est = object_size.estimate(subject)
    room = room_model.demo()
    if "--floor" in flags:                               # drop the table -> floor placement
        room.surfaces = [s for s in room.surfaces if s.cls != "table"]
    if "--clutter" in flags:                             # a mug sitting ON the table centre
        room.obstacles.append(room_model.Obstacle(
            bbox_min_m=(-0.05, 0.74, -1.30), bbox_max_m=(0.05, 0.86, -1.10), category="mug"))

    out = REPO / "studio" / "out" / "placer_smoke"
    out.mkdir(parents=True, exist_ok=True)
    spec = {
        "result_path": str(out / f"{slug}_result.json"),
        "out_dir": str(out), "render_preview": True,
        "hero": {"assetId": slug, "glb_in": str(glb),
                 "realSize_m": est["size_m"], "coverage": 0.65},
        "room": room.as_dict(),
    }
    spec_path = out / f"{slug}_spec.json"
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    print(f"subject={subject!r} asset={slug!r} realSize={est['size_m']} m "
          f"({est['source']}; {est.get('note', '')}) | flags={sorted(flags) or 'none'}")

    proc = subprocess.run(
        [BLENDER, "--background", "--python", str(REPO / "studio" / "blender" / "spatial_placer.py"),
         "--", str(spec_path)], capture_output=True, text=True, timeout=420)
    res_path = out / f"{slug}_result.json"
    if not res_path.exists():
        raise SystemExit(f"placer produced no result.\n{(proc.stderr or proc.stdout)[-800:]}")
    d = json.loads(res_path.read_text(encoding="utf-8")).get("done") or {}
    print(f"\nanchor={d.get('anchor')}  default={d.get('default')}  "
          f"defaultFits={d.get('defaultFits')}  obstacles={d.get('obstacles')}")
    for name, v in (d.get("variants") or {}).items():
        print(f"  [{name:5s}] fits={v['fits']!s:5s} pos={v['position_m']} "
              f"yaw={v['yaw_rad']} scale={v['scale']} size={v['size_m']}")
        print(f"          {v['reason']}")
    if d.get("preview"):
        print(f"\nPREVIEW -> {d['preview']}")


if __name__ == "__main__":
    main()
