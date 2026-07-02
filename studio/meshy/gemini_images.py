"""gemini_images.py — asset description -> consistent multi-view reference images.

For Meshy multi-image-to-3D we need several views of the SAME object. We generate
the FRONT view from text, then generate each other view image+text with the front
image as a reference ("same object, seen from the <angle>"), so the object's
identity/colors/proportions stay consistent across views. Clean white background.

    python studio/meshy/gemini_images.py                 # all segment assets
    python studio/meshy/gemini_images.py simple_pendulum  # just one

Images -> studio/out/meshy/<assetId>/view_{front,left,right,back}.png
"""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # for `import config`/`segment`
import config  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT_ROOT = REPO / "studio" / "out" / "meshy"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

# best image model first, then a proven fallback
MODELS = ["gemini-3-pro-image", "gemini-2.5-flash-image"]

VIEWS = [
    ("front", "front view, facing the camera straight on"),
    ("left",  "its exact left-side profile, rotated 90 degrees to the left"),
    ("right", "its exact right-side profile, rotated 90 degrees to the right"),
    ("back",  "its exact rear view, rotated 180 degrees"),
]

WHITE_BG = ("Plain solid pure-white seamless studio background, soft even lighting, the "
            "object centered and entirely visible, no other props, no text, no caption, "
            "minimal drop shadow. Photorealistic, sharp focus, neutral eye-level perspective.")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _call(parts, model: str) -> bytes | None:
    key = config.require("GEMINI_API_KEY")
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}
    req = urllib.request.Request(API.format(model=model, key=key),
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    r = urllib.request.urlopen(req, timeout=180)
    data = json.load(r)
    cands = data.get("candidates") or []
    if not cands:
        return None
    for pt in cands[0].get("content", {}).get("parts", []):
        if "inlineData" in pt:
            return base64.b64decode(pt["inlineData"]["data"])
    return None


def generate(parts) -> tuple[bytes, str]:
    """Try each model (best first) with light retry on transient errors."""
    last = "unknown"
    for model in MODELS:
        for attempt in range(2):
            try:
                img = _call(parts, model)
                if img:
                    return img, model
                last = f"{model}: no image part"
                break
            except urllib.error.HTTPError as e:
                last = f"{model}: HTTP {e.code} {e.read()[:160].decode('utf-8','ignore')!r}"
                if e.code in (429, 500, 503):
                    time.sleep(3); continue
                break
            except Exception as e:  # noqa: BLE001
                last = f"{model}: {e!r}"[:200]; time.sleep(2)
    raise RuntimeError(f"image generation failed — {last}")


def multiview(asset: dict, out_root: Path = OUT_ROOT) -> tuple[Path, dict]:
    out = out_root / asset["id"]
    out.mkdir(parents=True, exist_ok=True)
    desc = asset["desc"]
    used = {}

    front_prompt = f"Studio product photograph of {desc}. Front view. {WHITE_BG}"
    front, m = generate([{"text": front_prompt}])
    (out / "view_front.png").write_bytes(front)
    used["front"] = m

    for key, ang in VIEWS[1:]:
        prompt = (f"The reference image shows {desc}. Render the SAME identical object — "
                  f"identical colors, materials, proportions and every detail — but seen from "
                  f"{ang}. {WHITE_BG}")
        parts = [{"text": prompt}, {"inlineData": {"mimeType": "image/png", "data": _b64(front)}}]
        img, m = generate(parts)
        (out / f"view_{key}.png").write_bytes(img)
        used[key] = m
    return out, used


def main() -> int:
    import segment  # noqa: E402
    only = set(sys.argv[1:])
    for a in segment.ASSETS:
        if only and a["id"] not in only:
            continue
        t0 = time.time()
        out, used = multiview(a)
        print(f"[gemini] {a['id']:<16} {len(used)} views in {time.time()-t0:4.0f}s "
              f"(models: {sorted(set(used.values()))}) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
