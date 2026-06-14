"""object_size.py — ground an object's REAL-WORLD size (the "correct scale detection").

Today the pipeline sizes every generated asset by a hardcoded guess
(DEFAULT_SCALE_M = [0.4, 0.4, 0.4]) — a frog and a sofa both come out 0.4 m. That is
the root of "scale is wrong." This module asks the LLM for the subject's TRUE metric
dimensions so the placer can offer real-scale ("a 9 cm frog") and the fit-to-table
variant is computed from a real footprint, not a guess.

  estimate("frog")            -> {"size_m": [0.09, 0.05, 0.08], "source": "llm", ...}
  estimate("sofa")            -> ~[2.0, 0.85, 0.9]
  estimate("fire extinguisher")-> ~[0.18, 0.55, 0.18]

Pure stdlib + the Gemini REST endpoint (same pattern as director/llm.py). Cached to
studio/out/object_sizes.json so repeat subjects cost 0 calls. Never raises — returns a
sane fallback so the caller (asset pipeline / placer / solver) always has a size.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SECRETS = Path.home() / ".spatail" / "secrets.env"
_CACHE = _HERE.parents[1] / "studio" / "out" / "object_sizes.json"
MODEL = os.environ.get("SPATAIL_SIZE_MODEL", "gemini-2.5-flash")
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

MIN_M, MAX_M = 0.01, 12.0                 # clamp absurd answers (a bug, not a 0 mm bead)
FALLBACK = [0.3, 0.3, 0.3]                # last resort when offline + uncached


def _key() -> str:
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    if _SECRETS.exists():
        for line in _SECRETS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY=") and "=" in line:
                return line.split("=", 1)[1].strip()
    return ""


def _load_cache() -> dict:
    try:
        return json.loads(_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception:
        pass


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:64] or "object"


def _clamp3(v) -> list:
    try:
        out = [min(max(float(x), MIN_M), MAX_M) for x in v][:3]
    except Exception:
        return list(FALLBACK)
    return out if len(out) == 3 else list(FALLBACK)


PROMPT = """Give the TYPICAL real-world size of this object, as it naturally rests, in METRES.

Object: {subject}{extra}

Axes: width = left-to-right, height = vertical (floor to top), depth = front-to-back.
Use a representative adult/full-size example of the object in its normal resting pose
(an animal standing/sitting, furniture on the floor, a tool lying as used). Be realistic
— a frog is ~0.08 m, a mug ~0.10 m, a chair ~0.9 m tall, a car ~4.5 m long.

Return STRICT JSON ONLY:
{{"width_m": 0.0, "height_m": 0.0, "depth_m": 0.0, "note": "one short phrase"}}"""


def _ask_llm(subject: str, desc: str) -> dict | None:
    key = _key()
    if not key:
        return None
    extra = f" ({desc})" if desc and desc.lower() != subject.lower() else ""
    body = {"contents": [{"parts": [{"text": PROMPT.format(subject=subject, extra=extra)}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
    try:
        req = urllib.request.Request(API.format(model=MODEL, key=key),
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        data = json.load(urllib.request.urlopen(req, timeout=20))
        txt = "".join(p.get("text", "")
                      for p in data["candidates"][0]["content"]["parts"])
        d = json.loads(txt)
        size = _clamp3([d.get("width_m"), d.get("height_m"), d.get("depth_m")])
        return {"size_m": [round(x, 4) for x in size], "note": str(d.get("note") or "")[:120]}
    except Exception as e:  # noqa: BLE001
        print(f"[object_size] llm failed for {subject!r}: {e!r}"[:160])
        return None


def estimate(subject: str, desc: str = "", *, use_cache: bool = True) -> dict:
    """Return {size_m:[w,h,d] metres, source, note}. Cached; never raises."""
    subject = (subject or "").strip()
    if not subject:
        return {"size_m": list(FALLBACK), "source": "fallback", "note": "no subject"}
    key = _slug(subject)
    cache = _load_cache() if use_cache else {}
    if use_cache and key in cache:
        return {**cache[key], "source": "cache"}
    got = _ask_llm(subject, desc)
    if got is None:
        return {"size_m": list(FALLBACK), "source": "fallback", "note": "llm unavailable"}
    rec = {"size_m": got["size_m"], "source": "llm", "note": got["note"]}
    if use_cache:
        cache[key] = rec
        _save_cache(cache)
    return rec


if __name__ == "__main__":
    import sys
    for s in (sys.argv[1:] or ["frog", "sofa", "fire extinguisher", "coffee mug", "elephant"]):
        r = estimate(s)
        print(f"{s:24s} -> {r['size_m']}  ({r['source']}; {r['note']})")
