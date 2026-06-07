"""headless_build.py — run ONE generative build in its own headless Blender.

The per-phone / per-session executor. Where generator.generate() drives the single
shared LIVE Blender (the spine, one FIFO for everyone), this spawns a dedicated
``blender --background`` process per build so builds from different phones run
concurrently and in full isolation — "a Blender session per running app".

It mirrors studio/asset_factory/blender_factory._build: spec -> tempfile -> run the
headless driver -> read a result sidecar -> raise with the stderr tail on failure.
The driver (pipeline/blender/spatail_gen_headless_driver.py) reuses the exact
generator + llm_author authoring path, so the produced USDZ is byte-identical in
intent to the shared-spine path.

Returns the SAME dict shape as generator.generate so the job server's done-handler
is unchanged.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]                 # C:\SPATAIL_MAX
DRIVER = ROOT / "pipeline" / "blender" / "spatail_gen_headless_driver.py"
BLENDER_EXE = os.environ.get(
    "BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")
# Hard wall-clock cap for one isolated build (authoring + exec + export). Kept under
# the iOS awaitGeneratedModel cap (600s) so the phone sees a clean error, not a
# silent timeout. Override with SPATAIL_HEADLESS_BUILD_TIMEOUT.
BUILD_TIMEOUT = float(os.environ.get("SPATAIL_HEADLESS_BUILD_TIMEOUT", "540"))

_STAGE_PREFIX = "@@STAGE:"


def available() -> bool:
    """True iff we can actually spawn an isolated build (exe + driver present)."""
    return os.path.exists(BLENDER_EXE) and DRIVER.exists()


def build(prompt: str, job_id: str, out_dir, on_stage=lambda s: None,
          *, code: str | None = None) -> dict:
    """Spawn a dedicated headless Blender, build the prompt, return artifact info.

    Streams the driver's ``@@STAGE:`` lines to *on_stage* so the polling phone sees
    live progress. Raises RuntimeError on failure (no primitive fallback), with the
    driver's traceback / stderr tail attached.
    """
    if not os.path.exists(BLENDER_EXE):
        raise RuntimeError(f"Blender not found at {BLENDER_EXE!r} (set BLENDER_EXE).")
    if not DRIVER.exists():
        raise RuntimeError(f"headless driver missing at {DRIVER}")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fd, result_path = tempfile.mkstemp(suffix=".result.json")
    os.close(fd)
    try:
        os.remove(result_path)                             # start with no stale sidecar
    except OSError:
        pass

    spec = {"job_id": job_id, "out_dir": str(out_dir), "prompt": prompt,
            "code": code, "result_path": result_path}
    fd, spec_path = tempfile.mkstemp(suffix=".spec.json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(spec, f)

    cmd = [BLENDER_EXE, "--background", "--factory-startup",
           "--python", str(DRIVER), "--", spec_path]
    tail: list[str] = []                                   # rolling stderr/stdout tail for errors

    def _pump(stream, is_stage: bool) -> None:
        for line in iter(stream.readline, ""):
            line = line.rstrip("\n")
            if is_stage and line.startswith(_STAGE_PREFIX):
                try:
                    on_stage(line[len(_STAGE_PREFIX):])
                except Exception:                          # noqa: BLE001 — progress is best-effort
                    pass
            else:
                tail.append(line)
                if len(tail) > 80:
                    del tail[0]
        stream.close()

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            # UTF-8 so the model's arrows/em-dashes survive (matches llm_author).
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        t_out = threading.Thread(target=_pump, args=(proc.stdout, True), daemon=True)
        t_err = threading.Thread(target=_pump, args=(proc.stderr, False), daemon=True)
        t_out.start(); t_err.start()

        t0 = time.time()
        try:
            proc.wait(timeout=BUILD_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise RuntimeError(
                f"isolated build exceeded {BUILD_TIMEOUT:.0f}s and was killed "
                f"(job {job_id})")
        finally:
            t_out.join(timeout=2.0)
            t_err.join(timeout=2.0)

        if not os.path.exists(result_path):
            raise RuntimeError(
                f"headless build wrote no result for {job_id} "
                f"(exit {proc.returncode}, {time.time() - t0:.0f}s):\n"
                + "\n".join(tail[-25:]))

        sidecar = json.loads(Path(result_path).read_text(encoding="utf-8"))
        if not sidecar.get("ok"):
            raise RuntimeError(
                f"isolated build failed for {job_id}: {sidecar.get('error')}\n"
                f"{(sidecar.get('trace') or '')[-1500:]}")

        return {
            "usdz_name": sidecar["usdz"],
            "metadata_name": sidecar.get("metadata"),
            "bbox_yup": sidecar.get("bbox_yup"),
            "max_dim": sidecar.get("max_dim"),
            "authoring": sidecar.get("authoring"),
            "blender_summary": None,
        }
    finally:
        for p in (spec_path, result_path):
            try:
                os.remove(p)
            except OSError:
                pass
