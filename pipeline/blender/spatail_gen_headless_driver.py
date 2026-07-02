"""spatail_gen_headless_driver.py — one isolated, per-phone generative build.

Run INSIDE a dedicated headless Blender, one process per build:

    blender --background --python spatail_gen_headless_driver.py -- <spec.json>

This is the per-session ("a Blender session per running app") executor. Instead of
funnelling every phone through the single shared live Blender on :9876 (one FIFO,
everyone waits), the job server spawns one of these per build. Each gets its own
fresh Blender process, so builds from different phones run CONCURRENTLY and can
never stomp each other's scene.

It reuses the PROVEN authoring path verbatim — studio/server/generator.generate +
llm_author.author_scene — but injects an IN-PROCESS ``run_code`` (plain ``exec``)
in place of the socket bridge, since we're already inside bpy. Same PREPARE, same
self-repair loop, same USDZ export settings; only the place the code runs differs.

Spec JSON (written by studio/server/headless_build.py):
    {"job_id": str, "out_dir": str, "prompt": str,
     "code": str|null,        # optional: run this bpy script directly, skip the CLI
     "result_path": str}      # where to write the result sidecar

Result sidecar (read back by headless_build.py):
    {"ok": true, "usdz": "<name>.usdz", "metadata": "<name>_metadata.json",
     "bbox_yup": {...}, "max_dim": float, "authoring": {...}}
  or {"ok": false, "error": str, "trace": str}

Progress is streamed to stdout as ``@@STAGE:<text>`` lines so the parent can relay
live stages to the polling phone (the subprocess is otherwise opaque).
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]                 # C:\SPATAIL_MAX
sys.path.insert(0, str(ROOT / "studio" / "server"))


def _argv_after_ddash() -> list[str]:
    argv = sys.argv
    return argv[argv.index("--") + 1:] if "--" in argv else []


def _stage(text: str) -> None:
    # Streamed to the parent (headless_build.py) which relays to the phone.
    print(f"@@STAGE:{text}", flush=True)


def _local_run_code(code: str, *, timeout: float = 180.0, strict_json: bool = True) -> dict:
    """In-process twin of blender_bridge.run_code: exec ``code`` in a fresh
    namespace with ``result = {}`` predefined, return that dict, and raise with the
    full traceback on error (so llm_author's self-repair loop sees the failure and
    can ask Claude for a fix — exactly as the socket bridge behaves).

    ``timeout`` is accepted for signature parity but not enforced in-process; the
    parent subprocess applies the hard wall-clock cap instead.
    """
    ns: dict = {"result": {}}
    try:
        exec(code, ns)                                     # noqa: S102 — trusted authored bpy
    except Exception as exc:                               # noqa: BLE001
        raise RuntimeError(traceback.format_exc()) from exc
    res = ns.get("result", {})
    return res if isinstance(res, dict) else {"value": res}


def main() -> int:
    args = _argv_after_ddash()
    if not args:
        print("usage: blender --background --python spatail_gen_headless_driver.py -- <spec.json>",
              file=sys.stderr)
        return 2
    spec_path = Path(args[0])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    result_path = Path(spec["result_path"])

    try:
        import generator  # noqa: E402 — resolved via sys.path (studio/server)

        job_id = spec["job_id"]
        out_dir = Path(spec["out_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)

        code = spec.get("code")
        if code:
            # Direct mode: run a supplied bpy script (used for smoke tests and any
            # pre-authored builds). PREPARE -> exec -> EXPORT, all in-process.
            _stage("clearing scene")
            _local_run_code(generator._PREPARE, timeout=60.0)
            _stage("modeling")
            _local_run_code(code, timeout=120.0)
            _stage("exporting")
            usdz_path = out_dir / f"{job_id}.usdz"
            export_code = generator._EXPORT.replace("{USDZ}", str(usdz_path).replace("\\", "/"))
            res = _local_run_code(export_code, timeout=180.0)
            if not res.get("exported"):
                raise RuntimeError(f"USDZ export failed: {res.get('error')}")
            out = {"usdz_name": usdz_path.name, "metadata_name": None,
                   "bbox_yup": res.get("bbox_yup"), "max_dim": res.get("max_dim"),
                   "authoring": {"method": "direct", "attempts": 0}}
        else:
            # Real mode: Claude authors + self-repairs the bpy, run in THIS Blender.
            out = generator.generate(
                spec["prompt"], job_id, out_dir,
                on_stage=_stage, run_code=_local_run_code)

        result_path.write_text(json.dumps({
            "ok": True,
            "usdz": out["usdz_name"],
            "metadata": out.get("metadata_name"),
            "bbox_yup": out.get("bbox_yup"),
            "max_dim": out.get("max_dim"),
            "authoring": out.get("authoring"),
        }), encoding="utf-8")
        _stage("ready")
        return 0
    except Exception as exc:                               # noqa: BLE001
        try:
            result_path.write_text(json.dumps({
                "ok": False, "error": str(exc)[:500],
                "trace": traceback.format_exc()[-2000:],
            }), encoding="utf-8")
        except Exception:                                  # noqa: BLE001
            pass
        print(f"[headless_driver] build failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
