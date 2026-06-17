"""review_gemini.py — the HEADLESS reviewer (bpy-free). Reuses the Gemini REST +
strict-JSON pattern proven in studio/meshy/anchors.py.

This is the fallback for unattended/batch ingestion when no Claude session is in the
loop (the /spatail-asset-vision skill is the higher-fidelity default). It feeds Gemini
the SAME evidence the Claude reviewer sees — the contact sheet plus the labeled axis
views — and the SAME prompt (vision_report.reviewer_prompt), then validates the reply
into the canonical review shape so apply.py consumes it identically.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent / "meshy")):
    if p not in sys.path:
        sys.path.insert(0, p)

import vision_report as vr  # noqa: E402

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
MODEL = os.environ.get("SPATAIL_VISION_MODEL", "gemini-2.5-flash")

# The views Gemini sees, in addition to the contact sheet. Keeping it small (the
# contact sheet already shows everything) controls token cost + latency.
KEY_VIEWS = ("iso_01", "front", "left", "top", "normal_preview")


def _key() -> str:
    try:
        import config as mcfg          # studio/meshy/config
        k = mcfg.get("GEMINI_API_KEY")
        if k:
            return k
    except Exception:
        pass
    return os.environ.get("GEMINI_API_KEY", "")


def _img_part(path: Path) -> dict:
    return {"inlineData": {"mimeType": "image/png",
                           "data": base64.b64encode(path.read_bytes()).decode()}}


def _gemini_json(parts, *, timeout=120) -> dict:
    key = _key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing (~/.spatail/secrets.env)")
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.15}}
    last = "unknown"
    for _ in range(3):
        try:
            req = urllib.request.Request(API.format(model=MODEL, key=key),
                                         data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            data = json.load(urllib.request.urlopen(req, timeout=timeout))
            txt = "".join(p.get("text", "")
                          for p in data["candidates"][0]["content"]["parts"])
            return json.loads(txt)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.read()[:160].decode('utf-8', 'ignore')!r}"
            if e.code in (429, 500, 503):
                time.sleep(3); continue
            raise RuntimeError(f"gemini review failed — {last}")
        except Exception as e:  # noqa: BLE001
            last = repr(e)[:200]
            time.sleep(2)
    raise RuntimeError(f"gemini review failed — {last}")


def review(out_dir, report: dict, subject: str) -> dict:
    """Run the headless visual review. Returns a validated review dict; on any
    failure returns a neutral review (so apply.py degrades to no-rotate + object_size
    scale rather than crashing the batch)."""
    out_dir = Path(out_dir)
    asset_id = report.get("asset_id", out_dir.name)
    prompt = vr.reviewer_prompt(asset_id, subject, report)
    parts = [{"text": prompt}]
    cs = out_dir / "preview" / "contact_sheet.png"
    if cs.exists():
        parts += [{"text": "Contact sheet (all views + axes + measurements):"}, _img_part(cs)]
    for name in KEY_VIEWS:
        p = out_dir / "preview" / f"{name}.png"
        if p.exists():
            parts += [{"text": f"View: {name}"}, _img_part(p)]
    try:
        raw = _gemini_json(parts)
        return vr.validate_review(raw, reviewer=f"gemini:{MODEL}")
    except Exception as e:  # noqa: BLE001
        print(f"[review_gemini] {asset_id}: failed ({e}) — neutral review")
        return vr.empty_review(f"gemini review failed: {e}")


if __name__ == "__main__":
    # python studio/vision/review_gemini.py <out_dir/<slug>> [subject]
    od = Path(sys.argv[1])
    subj = sys.argv[2] if len(sys.argv) > 2 else od.name.replace("_", " ")
    rep = json.loads((od / "reports" / "asset_report.json").read_text(encoding="utf-8"))
    r = review(od, rep, subj)
    (od / "reports" / "visual_review_result.json").write_text(json.dumps(r, indent=2), encoding="utf-8")
    print(json.dumps(r, indent=2))
