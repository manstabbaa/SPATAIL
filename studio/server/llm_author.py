"""llm_author.py — real generative authoring: Claude writes the Blender code.

Given a free-text prompt, ask Claude (via the locally-installed `claude` CLI in
headless print mode, using the user's existing Claude Code login — no API key) to
author ONE Blender-Python script that MODELS a recognizable representation of the
subject and ANIMATES the described action as a seamless baked loop. The script is
executed in the LIVE Blender over the MCP socket bridge (by the caller). If
Blender reports an error, the traceback is fed back to Claude for a corrected
script (self-repair), up to `max_attempts`.

This is what makes the loop actually generative: "an apple falling from a tree"
yields a modelled tree + apple with the apple detaching and falling — not a grey
sphere from a keyword table.
"""
from __future__ import annotations

import os
import re
import subprocess

# Resolve the claude CLI: env override, else the known install, else PATH.
_DEFAULT_CLI = r"C:\Users\manst\AppData\Roaming\Claude\claude-code\2.1.156\claude.exe"


def cli_path() -> str | None:
    p = os.environ.get("SPATAIL_CLAUDE_CLI")
    if p and os.path.exists(p):
        return p
    if os.path.exists(_DEFAULT_CLI):
        return _DEFAULT_CLI
    # fall back to PATH lookup
    from shutil import which
    return which("claude")


def available() -> bool:
    return cli_path() is not None


def _run_cli(prompt_text: str, timeout: float) -> str:
    """Run `claude -p` headlessly, prompt on stdin, return stdout text."""
    cli = cli_path()
    if not cli:
        raise RuntimeError("claude CLI not found (set SPATAIL_CLAUDE_CLI)")
    args = [cli, "-p", "--output-format", "text"]
    model = os.environ.get("SPATAIL_GEN_MODEL")
    if model:
        args += ["--model", model]
    proc = subprocess.run(args, input=prompt_text, capture_output=True,
                          text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI exit {proc.returncode}: {(proc.stderr or '').strip()[:500]}")
    return proc.stdout or ""


def _strip_fences(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```[a-zA-Z0-9]*\s*\n(.*)\n```\s*$", t, flags=re.DOTALL)
    if m:
        return m.group(1).strip()
    t = re.sub(r"^```[a-zA-Z0-9]*\s*\n", "", t)
    t = re.sub(r"\n```\s*$", "", t)
    return t.strip()


_RULES = """You are a senior technical 3D artist authoring scenes in Blender 5.1 via \
its Python API (bpy). Write ONE self-contained Python script that models and \
animates exactly what is requested, to run inside a LIVE Blender.

HARD RULES — the script MUST:
1. import bpy, math, and `from mathutils import Vector` itself.
2. NOT clear the scene; NOT call bpy.ops.wm.read_homefile / read_factory_settings \
/ read_userpref / quit_blender. The scene is ALREADY empty and an Empty named \
"gen_root" already exists at the world origin.
3. Parent EVERY object you create to gen_root via the data API:
       root = bpy.data.objects["gen_root"]
       obj.parent = root
   Do not move/rotate/scale gen_root yourself.
4. MODEL a clearly recognizable representation of the subject from mesh primitives \
(sphere, cube, cylinder, cone, torus...), with correct proportions and sensible \
colours: one Principled BSDF material per part — set inputs "Base Color", \
"Roughness", "Metallic", and also set mat.diffuse_color to the same rgba. Compose \
multiple parts when it reads better (a tree = trunk + foliage; an apple = body + stem + leaf).
5. ANIMATE the described action as a SEAMLESS LOOP over frames 1..%(FRAMES)d at \
%(FPS)d fps — the last-frame pose must flow into frame 1 with no visible jump. Bake \
with obj.keyframe_insert(...). Set interpolation at INSERT time:
       ed = bpy.context.preferences.edit
       prev = ed.keyframe_new_interpolation_type
       ed.keyframe_new_interpolation_type = 'BEZIER'   # or 'LINEAR'
   and restore prev at the end. (Blender 5.1 slotted actions have NO Action.fcurves, \
so never post-process fcurves.)
6. Keep it TABLETOP-sized: whole scene within ~0.9 m in its largest dimension, \
built around the origin and resting on the ground (nothing below z=0 at rest). Do \
NOT change scene frame_start/frame_end/fps.
7. End by assigning a short summary dict to a variable named `result`, e.g.
       result = {"objects": [o.name for o in bpy.data.objects], "summary": "..."}

OUTPUT: return ONLY the raw Python script — no prose, no markdown fences."""


def _author_prompt(prompt, frames, fps):
    rules = _RULES % {"FRAMES": frames, "FPS": fps}
    return (rules + "\n\n---\nNOW AUTHOR THIS: \"" + prompt + "\"\n"
            "Model it recognizably and animate the action as a seamless "
            f"{frames}-frame loop at {fps} fps. Return only the Python script.")


def _repair_prompt(prompt, frames, fps, code, error):
    return (_author_prompt(prompt, frames, fps) +
            "\n\n---\nYour previous script raised this error in Blender:\n\n" +
            error[:2500] + "\n\nPREVIOUS SCRIPT:\n" + code[:6000] +
            "\n\nReturn a corrected, complete Python script (only code). Keep every "
            "HARD RULE (parent to gen_root, assign `result`, no scene clearing).")


def author_scene(prompt, frames, fps, run_code, on_stage=lambda s: None,
                 max_attempts=3, gen_timeout=240.0, exec_timeout=150.0):
    """Author + execute the scene in live Blender. Returns a dict; raises
    RuntimeError if the CLI is unavailable or all attempts fail."""
    if not available():
        raise RuntimeError("claude CLI not available for authoring")

    code = None
    last_err = None
    for attempt in range(1, max_attempts + 1):
        on_stage("modeling" if attempt == 1 else f"repairing (try {attempt})")
        text = _author_prompt(prompt, frames, fps) if attempt == 1 \
            else _repair_prompt(prompt, frames, fps, code, last_err)
        try:
            raw = _run_cli(text, timeout=gen_timeout)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"authoring call failed: {type(e).__name__}: {e}") from e

        code = _strip_fences(raw)
        if not code:
            last_err = "Claude returned empty output"
            continue
        try:
            res = run_code(code, timeout=exec_timeout)
            return {"method": "llm", "attempts": attempt, "code": code,
                    "blender_result": res}
        except Exception as e:  # noqa: BLE001 — bridge raises the Blender traceback
            last_err = str(e)

    raise RuntimeError(f"LLM authoring failed after {max_attempts} attempts: {last_err}")
