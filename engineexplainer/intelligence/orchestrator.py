"""The orchestrator — prompt in, spatial contract out.

This file wires the four roles together. Each role's LLM call is isolated
behind a tiny function so they can be swapped (Anthropic, local, fixture)
without touching the flow.

For now, `_call_llm` is a stub that the user wires to their preferred
client (Anthropic SDK by default). The flow + validation logic is real.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .context_builder import build_context
from .semantic_validator import validate as semantic_validate
from .bake_bridge import maybe_bake_from_contract
from .tools.contract_actions import ContractBuilder, DirectorError

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


# ---------------------------------------------------------------------------
# LLM call boundary (swap this for your real client)
# ---------------------------------------------------------------------------

def _call_llm(system_prompt: str, user_message: str, *,
              model: str | None = None,
              max_tokens: int = 4000) -> str:
    # Default: Opus for the director (heavy spatial reasoning) and critic;
    # callers can override per-call to use Sonnet for the cheaper mechanic step.
    model = model or os.environ.get("ENGINEEXPLAINER_MODEL", "claude-opus-4-5")
    """Single point of contact with whatever model client is in use.

    Default implementation uses the Anthropic SDK if installed; otherwise
    raises so the caller knows to wire it up. The intelligence layer
    contract is: input is two strings, output is a string. Nothing else.
    """
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "anthropic SDK not installed — `pip install anthropic` "
            "or replace `_call_llm` with your client of choice."
        ) from e

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY env
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return "".join(block.text for block in resp.content if hasattr(block, "text"))


def _read_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Role 1: mechanic — writes the technical answer
# ---------------------------------------------------------------------------

def run_mechanic(context: dict) -> dict:
    sys_prompt = _read_prompt("mechanic")
    user_msg = (
        f"USER QUESTION:\n{context['prompt']}\n\n"
        f"AVAILABLE PARTS (id → role → world position):\n"
        f"{json.dumps(_summarise_registry(context['part_registry']), indent=2)}\n\n"
        f"AVAILABLE ANIMATIONS (id → depicts):\n"
        f"{json.dumps(_summarise_animations(context['animation_library']), indent=2)}\n\n"
        f"RECENT HISTORY:\n{json.dumps(context['history'], indent=2)}\n\n"
        f"Respond with the JSON described in your system prompt. No commentary."
    )
    # Mechanic is cheap and doesn't need spatial reasoning — use Sonnet to save tokens.
    raw = _call_llm(sys_prompt, user_msg, model="claude-sonnet-4-5")
    return _parse_json_block(raw)


# ---------------------------------------------------------------------------
# Role 2: director — emits tool calls. We give it a constrained interface
# by serialising the Mechanic's plan + the registry, then asking it to
# return a Python-flavoured pseudo-script we exec against a ContractBuilder.
# ---------------------------------------------------------------------------

def run_director(context: dict, mechanic_plan: dict) -> dict:
    """Returns a draft contract dict."""
    sys_prompt = _read_prompt("director")
    hints = context["part_registry"].get("director_hints", {})
    user_msg = (
        f"USER QUESTION:\n{context['prompt']}\n\n"
        f"ASSET:\n  id: {context['asset_id']}\n  glb: {context['asset_glb']}\n\n"
        f"ASSET HINTS (HONOR THESE — they tell you what's appropriate for this specific asset):\n"
        f"{json.dumps(hints, indent=2)}\n\n"
        f"MECHANIC'S ANSWER:\n{json.dumps(mechanic_plan, indent=2)}\n\n"
        f"PART REGISTRY (full):\n"
        f"{json.dumps(_summarise_registry(context['part_registry'], full=True), indent=2)}\n\n"
        f"ANIMATION LIBRARY (full):\n"
        f"{json.dumps(context['animation_library'].get('animations', {}), indent=2)}\n\n"
        f"CAMERA PRESETS available to you:\n{context['camera_presets']}\n"
        f"(But filter through the asset hints' preferred_camera_presets / avoid_presets.)\n\n"
        "Reply ONLY with a single fenced ```python block of tool calls "
        "against the variable `ctx` (a ContractBuilder). Start with ctx.scene(...), "
        "then alternate ctx.beat(...) and action calls. End with the title and "
        "summary stored on ctx.title and ctx.summary."
    )
    raw = _call_llm(sys_prompt, user_msg, max_tokens=6000)
    return _execute_director_script(raw, context, mechanic_plan)


def _execute_director_script(raw: str, context: dict, plan: dict) -> dict:
    """Exec the director's script against a fresh ContractBuilder. Surface
    DirectorError messages back up so the orchestrator can retry."""
    code = _extract_python_block(raw)
    ctx = ContractBuilder(
        prompt=context["prompt"],
        part_registry=context["part_registry"],
        animation_library=context["animation_library"],
    )
    ctx.title = plan.get("title", "")
    ctx.summary = plan.get("summary", "")

    # Wrap ctx in a kwarg-normalising proxy so the LLM can use either Python's
    # snake_case (`start_at`) or the contract JSON's camelCase (`startAt`).
    # This kills a whole class of API-drift bugs in one place.
    proxy = _CtxProxy(ctx)

    safe_globals: dict[str, Any] = {"__builtins__": {"len": len, "range": range, "min": min, "max": max}}
    exec(code, safe_globals, {"ctx": proxy})
    return ctx.finish(title=ctx.title, summary=ctx.summary)


def _camel_to_snake(name: str) -> str:
    """startAt → start_at,  conRodLength → con_rod_length"""
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and not name[i-1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


# Common synonyms the LLM tends to invent vs. what the API actually exposes.
# Applied after camelCase→snake_case so we catch the rest in one pass.
_KWARG_ALIASES = {
    "visibility_hide": "hide",
    "visibility_show": "show",
    "to_pose": "to_pose",   # accepted as-is by move_camera
    "from": "from_",        # 'from' is reserved; LLM occasionally tries it (would have errored at parse)
    "from_pos": "from_",
    "except": "except_",
    "exception": "except_",
}


class _Absorbent:
    """Null-object: any operation against it returns itself or a benign default.
       Lets the director's hallucinated inline computations (subscripting,
       chained calls, arithmetic on missing positions) silently no-op
       instead of raising — so the rest of the contract still builds."""
    def __getitem__(self, k): return self
    def __getattr__(self, k): return self
    def __call__(self, *a, **kw): return self
    def __iter__(self): return iter([0.0, 0.0, 0.0])
    def __len__(self): return 3
    def __bool__(self): return False
    def __float__(self): return 0.0
    def __int__(self): return 0
    def __str__(self): return ""
    def __add__(self, o): return self
    __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = __truediv__ = __rtruediv__ = __add__


class _CtxProxy:
    """Wraps ContractBuilder so every method call gets its kwargs normalised
       (camelCase → snake_case + synonyms) and unknown methods become silent
       no-ops returning _Absorbent. Real contract-building methods are what
       count — everything else we soak up so the director's bugs don't kill
       an otherwise-valid contract."""

    def __init__(self, target):
        object.__setattr__(self, "_target", target)
        parts_dict = target.part_registry.get("parts", {})
        object.__setattr__(self, "_helpers", {
            "get_part_position": lambda pid: (parts_dict.get(pid, {}).get("world_position") or [0.0, 0.0, 0.0]),
            "get_part":          lambda pid: parts_dict.get(pid, {}),
            "parts_in_region":   lambda region: [pid for pid, p in parts_dict.items() if p.get("region") == region],
            "parts_with_role":   lambda role:   [pid for pid, p in parts_dict.items() if p.get("role") == role],
            "list_parts":        lambda: list(parts_dict.keys()),
        })

    def __getattr__(self, name):
        try:
            attr = getattr(self._target, name)
        except AttributeError:
            if name in self._helpers:
                return self._helpers[name]
            def _noop(*a, **kw):
                print(f"[director:noop] ctx.{name}({a!r}, {kw!r})")
                return _Absorbent()
            return _noop

        if not callable(attr):
            return attr
        def wrapped(*args, **kwargs):
            fixed = {}
            for k, v in kwargs.items():
                k2 = _camel_to_snake(k) if any(c.isupper() for c in k) else k
                k2 = _KWARG_ALIASES.get(k2, k2)
                fixed[k2] = v
            try:
                return attr(*args, **fixed)
            except DirectorError:
                raise  # keep these — they trigger retry
            except Exception as e:
                # Soft-fail other errors so one bad action doesn't kill the whole contract.
                print(f"[director:soft-fail] ctx.{name}({args!r}, {fixed!r}): {e}")
                return _Absorbent()
        return wrapped

    def __setattr__(self, name, value):
        setattr(self._target, name, value)


# ---------------------------------------------------------------------------
# Role 3: critic — validates the draft contract
# ---------------------------------------------------------------------------

def run_critic(context: dict, draft: dict) -> dict:
    sys_prompt = _read_prompt("critic")
    user_msg = (
        f"DRAFT CONTRACT:\n{json.dumps(draft, indent=2)}\n\n"
        f"PART REGISTRY KEYS: {list(context['part_registry'].get('parts', {}).keys())}\n"
        f"ANIMATION KEYS: {list(context['animation_library'].get('animations', {}).keys())}\n"
    )
    raw = _call_llm(sys_prompt, user_msg, max_tokens=1000)
    return _parse_json_block(raw)


# ---------------------------------------------------------------------------
# The full pipeline
# ---------------------------------------------------------------------------

def answer(prompt: str, *, history: list[str] | None = None,
           asset_id: str | None = None,
           max_revisions: int = 1) -> dict:
    """End-to-end: prompt → contract dict (matches spatial-contract.schema.json).

    asset_id selects which per-asset registry + animation library the
    pipeline sees. Without it, the LLM gets the engine context and would
    write engine contracts even when the fan is loaded.

    Pipeline (with two gates):
        mechanic → director → critic (schema) → semantic_validator (Sonnet, content) → contract
    """
    context = build_context(prompt, history=history, asset_id=asset_id)
    print(f"[orchestrator] asset_id={context['asset_id']}  parts={len(context['part_registry'].get('parts', {}))}  animations={list(context['animation_library'].get('animations', {}).keys())}")

    mechanic_plan = run_mechanic(context)

    last_error = None
    semantic_review = None
    for attempt in range(1 + max_revisions):
        try:
            draft = run_director(context, mechanic_plan)
        except DirectorError as e:
            last_error = str(e)
            mechanic_plan = {**mechanic_plan, "_previous_attempt_error": last_error}
            continue

        # Cheap schema critic
        critique = run_critic(context, draft)

        # Semantic / content gate — does the contract answer the question?
        try:
            semantic_review = semantic_validate(draft, context["part_registry"], prompt)
        except Exception as e:
            print(f"[orchestrator] semantic validator errored: {e}; shipping draft as-is")
            semantic_review = None

        ok_schema = critique.get("verdict") == "OK"
        ok_semantic = semantic_review.is_ok() if semantic_review else True

        if ok_schema and ok_semantic:
            # Bake-bridge: if the director emitted any bake_animation actions
            # (declaring clips that don't exist yet), run them now via Blender
            # subprocess + re-export GLB + patch the contract to reference
            # the freshly-baked clips. No-op if the contract has no bakes.
            try:
                baked = maybe_bake_from_contract(draft)
                if baked:
                    print(f"[orchestrator] baked {len(baked)} clip(s): {list(baked.keys())}")
            except Exception as e:
                # Don't block contract delivery on a bake failure — the
                # runtime will gracefully ignore unknown animation names.
                print(f"[orchestrator] bake bridge errored: {e}")
                draft.setdefault("meta", {})["_bake_error"] = str(e)
            return draft

        if attempt == max_revisions:
            # Out of retries — ship with both reviews attached so the runtime
            # / inspector can see why playback may underwhelm.
            if not ok_schema:
                draft["meta"]["_critic_issues"] = critique.get("issues", [])
            if semantic_review and not ok_semantic:
                draft["meta"]["_semantic_issues"] = [
                    {"beat": i.beat, "kind": i.kind, "fix": i.fix}
                    for i in semantic_review.issues
                ]
                draft["meta"]["_semantic_summary"] = semantic_review.summary
            return draft

        # Feed both critiques back into the next director attempt
        feedback = {**mechanic_plan}
        if not ok_schema:
            feedback["_critic_issues"] = critique.get("issues", [])
        if semantic_review and not ok_semantic:
            feedback["_semantic_brief"] = semantic_review.to_director_brief()
        mechanic_plan = feedback

    raise RuntimeError(f"Contract authoring failed after {max_revisions+1} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _summarise_registry(reg: dict, *, full: bool = False) -> dict:
    """Registry view for the LLM. Uses the curated hero_parts + aliases if
    they exist (set by authoring/curate_hero_parts.py); falls back to a
    size-sorted sample otherwise.

    The full 673-part V8 registry contains tons of bolts/brackets that the
    director shouldn't be reasoning over — addressing the wrong one wastes
    a beat. By restricting the visible vocabulary to ~10 hand-picked
    components, the director's choices are bounded and the visual outcome
    is dramatically more reliable.
    """
    parts   = reg.get("parts", {})
    aliases = reg.get("aliases", {})
    hero    = reg.get("hero_parts")  # set by the curator; may be absent on a fresh asset
    bbox    = reg.get("engine_bbox", {})

    if hero:
        # Curated path — show ONLY hero parts to the LLM.
        addressable = {
            pid: {k: hero[pid][k] for k in ("role", "region", "world_position", "size_m") if k in hero[pid]}
            for pid in hero
        }
    else:
        # Uncurated fallback: top 40 by size (some classification preference)
        def sample_key(item):
            _, p = item
            return (-int(p.get("role", "unclassified") != "unclassified"), -p.get("size_m", 0))
        sampled = dict(sorted(parts.items(), key=sample_key)[:40])
        addressable = {
            pid: {k: p[k] for k in ("role", "region", "world_position", "size_m") if k in p}
            for pid, p in sampled.items()
        }

    summary = {
        "aliases": aliases,
        "hero_parts": addressable,
        "engine_bbox": bbox,
        "total_parts_in_glb": len(parts),
        "note": (
            "Reference parts ONLY by alias (preferred) or by an id in `hero_parts` above. "
            "There are 600+ other meshes in the GLB (bolts, brackets, washers) but they're "
            "too small to label meaningfully and the classification on them is noisy."
        ),
    }
    if not full:
        # Mechanic's leaner view — just aliases + their roles
        return {
            "aliases": aliases,
            "hero_part_count": len(addressable),
            "engine_bbox": bbox,
        }
    return summary


def _summarise_animations(lib: dict) -> dict:
    return {aid: a.get("description", "") for aid, a in lib.get("animations", {}).items()}


def _parse_json_block(raw: str) -> dict:
    """Be forgiving about LLMs wrapping JSON in code fences or prose."""
    raw = raw.strip()
    if "```" in raw:
        # Take the first fenced block's contents
        parts = raw.split("```")
        for chunk in parts[1::2]:
            chunk = chunk.lstrip()
            if chunk.startswith("json"): chunk = chunk[4:]
            chunk = chunk.strip()
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                continue
    return json.loads(raw)


def _extract_python_block(raw: str) -> str:
    if "```" not in raw:
        code = raw.strip()
    else:
        parts = raw.split("```")
        code = raw.strip()
        for chunk in parts[1::2]:
            chunk = chunk.lstrip()
            if chunk.startswith("python"): chunk = chunk[6:]
            code = chunk.strip()
            break
    # Opus likes to use proper Unicode typography (em-dashes, smart quotes,
    # math minus) which is invalid Python source. Normalise to ASCII before
    # exec — otherwise SyntaxError on the first negative literal.
    UNICODE_FIXES = {
        "−": "-",   # math minus
        "‐": "-", "‑": "-", "‒": "-",   # hyphens
        "–": "-",   # en-dash
        "—": "-",   # em-dash
        "―": "-",   # horizontal bar
        "‘": "'", "’": "'",   # smart single quotes
        "“": '"', "”": '"',   # smart double quotes
        " ": " ",   # nbsp
        "′": "'", "″": '"',   # primes
        "×": "*", "÷": "/",   # math multiply/divide
    }
    for u, a in UNICODE_FIXES.items():
        code = code.replace(u, a)
    return code
