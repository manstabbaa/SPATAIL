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

# Resolve the claude CLI: env override -> newest versioned install -> PATH.
# The Claude Code app installs to ...\Claude\claude-code\<version>\claude.exe and
# auto-updates, so we glob for the newest version dir rather than pin one.
def _newest_app_cli() -> str | None:
    import glob
    roots = [
        os.path.expandvars(r"%APPDATA%\Claude\claude-code"),
        os.path.expandvars(r"%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code"),
    ]
    candidates = []
    for r in roots:
        candidates += glob.glob(os.path.join(r, "*", "claude.exe"))
    if not candidates:
        return None

    def ver_key(path):
        ver = os.path.basename(os.path.dirname(path))
        parts = []
        for chunk in ver.split("."):
            parts.append(int(chunk) if chunk.isdigit() else 0)
        return parts
    return sorted(candidates, key=ver_key)[-1]


def cli_path() -> str | None:
    p = os.environ.get("SPATAIL_CLAUDE_CLI")
    if p and os.path.exists(p):
        return p
    newest = _newest_app_cli()
    if newest:
        return newest
    from shutil import which
    return which("claude")


def available() -> bool:
    return cli_path() is not None


def _run_cli(prompt_text: str, timeout: float) -> str:
    """Run `claude -p <prompt>` headlessly, return stdout text.

    The prompt is passed as a POSITIONAL ARG, not on stdin. Measured on this CLI
    build (2.1.156): the stdin form is ~2.5x slower (58s vs 23s on the real
    authoring prompt) because the CLI waits on stdin buffering/EOF before starting.
    All our prompts are <=~11KB, far under Windows' 32767-char command-line limit,
    so the arg form is safe and much faster. stdin is closed to avoid any wait.
    """
    cli = cli_path()
    if not cli:
        raise RuntimeError("claude CLI not found (set SPATAIL_CLAUDE_CLI)")
    args = [cli, "-p", prompt_text, "--output-format", "text"]
    model = os.environ.get("SPATAIL_GEN_MODEL")
    if model:
        args += ["--model", model]
    # Force UTF-8 for the CLI's stdout. Without this, subprocess uses the Windows
    # ANSI code page (cp1252) and mangles non-ASCII the model emits (arrows →,
    # em-dashes —, emoji) into mojibake baked into the spec / Blender code.
    # stdin=DEVNULL so the CLI never blocks waiting for input.
    proc = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")
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


def ask_json(system_prompt: str, user_prompt: str, *, timeout: float = 180.0,
             max_attempts: int = 2) -> dict:
    """Ask Claude (headless CLI) for a JSON object and return it parsed.

    Used by the Director to PLAN an experience (structure), distinct from
    author_scene which writes Blender code. Retries once if the reply doesn't
    parse as JSON. Raises RuntimeError if the CLI is unavailable or no valid
    JSON is produced.
    """
    import json
    if not available():
        raise RuntimeError("claude CLI not available")
    text = system_prompt + "\n\n" + user_prompt
    last = ""
    for attempt in range(1, max_attempts + 1):
        raw = _run_cli(text, timeout=timeout)
        body = _strip_fences(raw)
        # tolerate prose around the object: grab the outermost {...}
        start, end = body.find("{"), body.rfind("}")
        candidate = body[start:end + 1] if (start != -1 and end > start) else body
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            last = f"{e}: {candidate[:200]}"
            text = (system_prompt + "\n\n" + user_prompt +
                    "\n\n---\nYour previous reply was not valid JSON (" + str(e) +
                    "). Return ONLY a single valid JSON object, no prose, no fences.")
    raise RuntimeError(f"could not get valid JSON from Claude: {last}")


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
8. STYLE — aim for a soft, matte "clay" look in a gentle pastel palette (e.g. soft \
blush, muted blue, sage green, warm sand, clay terracotta, dusty lilac, cream, slate) \
where it does not hurt recognizability (a banana stays yellow, the sky stays blue). \
Prefer clean solid forms over tiny sharp detail. SPATAIL applies a final matte+bevel \
pass automatically, so you do NOT need to add bevels or tune roughness yourself.
9. SOCKETS (optional) — you MAY add Empties named "socket_<purpose>" (e.g. \
socket_label, socket_grab), parented to gen_root, to mark attachment points for UI \
or handles. They are invisible and never animated.

OUTPUT: return ONLY the raw Python script — no prose, no markdown fences."""


def _author_prompt(prompt, frames, fps):
    rules = _RULES % {"FRAMES": frames, "FPS": fps}
    return (rules + "\n\n---\nNOW AUTHOR THIS: \"" + prompt + "\"\n"
            "Model it recognizably and animate the action as a seamless "
            f"{frames}-frame loop at {fps} fps. Return only the Python script.")


def _repair_prompt(prompt, frames, fps, code, error):
    return (_author_prompt(prompt, frames, fps) +
            "\n\n---\nYour previous script raised this error in Blender:\n\n" +
            (error or "")[:2500] + "\n\nPREVIOUS SCRIPT:\n" + (code or "")[:6000] +
            "\n\nReturn a corrected, complete Python script (only code). Keep every "
            "HARD RULE (parent to gen_root, assign `result`, no scene clearing).")


def author_scene(prompt, frames, fps, run_code, on_stage=lambda s: None,
                 max_attempts=2, gen_timeout=130.0, exec_timeout=120.0):
    """Author + execute the scene in live Blender. Returns a dict; raises
    RuntimeError if the CLI is unavailable or all attempts fail.

    Latency on this CLI varies a lot (measured 27s–132s for the same prompt class,
    and the heavy default/sonnet model can exceed 300s). So:
      - gen_timeout is a TIGHTER per-call cap (default 130s) and a timeout is a
        RETRYABLE attempt, not a fatal error — a slow roll gets a fresh, faster try
        rather than burning the whole budget.
      - fewer attempts (2) keeps the worst case bounded for the total experience
        budget (the iOS client caps a full lesson at 600s).
    Set SPATAIL_GEN_MODEL to force a faster model for authoring.
    """
    if not available():
        raise RuntimeError("claude CLI not available for authoring")

    code = None
    last_err = None
    for attempt in range(1, max_attempts + 1):
        # Repair ONLY when a prior attempt actually produced a script. A prior
        # attempt that TIMED OUT leaves code=None; building a repair prompt from
        # that would do code[:6000] on None -> "'NoneType' object is not
        # subscriptable". In that case retry with a fresh author prompt instead.
        repairing = bool(code)
        on_stage("modeling" if not repairing else f"repairing (try {attempt})")
        text = _repair_prompt(prompt, frames, fps, code, last_err) if repairing \
            else _author_prompt(prompt, frames, fps)
        try:
            raw = _run_cli(text, timeout=gen_timeout)
        except subprocess.TimeoutExpired:
            # a slow roll — retry (don't kill the station) unless out of attempts
            last_err = f"authoring call exceeded {gen_timeout:.0f}s"
            continue
        except Exception as e:  # noqa: BLE001 — CLI/process failure
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
