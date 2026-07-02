"""Typed action constructors the Director calls. Each one validates against
the loaded part registry + animation library, then appends a record onto
the in-progress contract. If validation fails, the Director sees the
error message immediately and can retry.

Why a tool interface and not free-form JSON: it eliminates the entire
class of "generates a contract that references a part that doesn't
exist" failures, which is the #1 failure mode in agent-emits-JSON systems.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class DirectorError(ValueError):
    """Raised back at the Director when a tool call references something
    that isn't in the registry or library. The orchestrator catches this
    and lets the Director retry with the error message in scope."""


# ---------------------------------------------------------------------------
# Build context — the object the Director holds for the duration of one
# contract authoring session.
# ---------------------------------------------------------------------------

@dataclass
class ContractBuilder:
    """The Director's working surface.

    Lifecycle:
        ctx = ContractBuilder(prompt, registry, library)
        ctx.scene(...)                  # initial scene state
        ctx.beat("intro", "narration text", duration=4)
        ctx.highlight("piston_1A")
        ctx.label("piston_1A", "Piston", kicker="COMPONENT")
        ctx.beat("next", "...", duration=5)
        ...
        contract = ctx.finish(title=..., summary=...)
    """

    prompt: str
    part_registry: dict
    animation_library: dict
    title: str = ""
    summary: str = ""
    scene_state: dict = field(default_factory=dict)
    beats: list[dict] = field(default_factory=list)
    _current_beat: dict | None = None

    # ---- registry helpers -------------------------------------------------

    def _valid_part_ids(self) -> set[str]:
        return set(self.part_registry.get("parts", {}).keys())

    def _valid_anim_ids(self) -> set[str]:
        return set(self.animation_library.get("animations", {}).keys())

    def _aliases(self) -> dict[str, str]:
        return self.part_registry.get("aliases", {}) or {}

    def _resolve(self, ref):
        """Swap aliases for real part ids. Recursive over lists."""
        if isinstance(ref, list):
            return [self._resolve(r) for r in ref]
        if isinstance(ref, str):
            aliases = self._aliases()
            return aliases.get(ref, ref)
        return ref

    def _check_part_ref(self, ref) -> None:
        """A part ref can be a single id, a glob, an alias, or a list of any.
        Raises DirectorError if none of the listed/matched ids resolve."""
        if ref is None:
            return
        if isinstance(ref, list):
            for r in ref:
                self._check_part_ref(r)
            return
        if not isinstance(ref, str):
            raise DirectorError(f"part ref must be string or list, got {type(ref).__name__}")
        # Resolve alias if any
        aliases = self._aliases()
        if ref in aliases:
            ref = aliases[ref]
        valid = self._valid_part_ids()
        if "*" in ref:
            if not any(fnmatch.fnmatch(p, ref) for p in valid):
                close = _closest(ref.replace("*", ""), valid)
                raise DirectorError(f"part pattern '{ref}' matched nothing. Closest existing: {close}")
            return
        if ref not in valid:
            # One more chance: case-insensitive match
            ref_lower = ref.lower()
            for v in valid:
                if v.lower() == ref_lower:
                    return
            close = _closest(ref, valid)
            available_aliases = list(aliases.keys())
            raise DirectorError(
                f"part '{ref}' not in registry. "
                f"Closest existing ids: {close}. "
                f"Available aliases: {available_aliases}"
            )

    def _check_anim_ref(self, name: str) -> None:
        valid = self._valid_anim_ids()
        if name not in valid:
            close = _closest(name, valid)
            raise DirectorError(f"animation '{name}' not in library. Closest existing: {close}")

    # ---- scene-level setup ------------------------------------------------

    def scene(self, *, hide=None, show=None, visibility_hide=None, visibility_show=None,
              camera_preset=None, camera_from=None, camera_to=None, camera_fov=None,
              background=None) -> None:
        # Accept both `hide=` and `visibility_hide=` — the director's natural
        # phrasing varies and we don't want to crash on a synonym.
        hide = hide if hide is not None else visibility_hide
        show = show if show is not None else visibility_show
        self._check_part_ref(hide)
        self._check_part_ref(show)
        scene: dict[str, Any] = {}
        vis: dict[str, list] = {}
        if hide: vis["hide"] = hide if isinstance(hide, list) else [hide]
        if show: vis["show"] = show if isinstance(show, list) else [show]
        if vis: scene["visibility"] = vis
        if camera_preset:
            scene["camera"] = {"preset": camera_preset, **({"fov": camera_fov} if camera_fov else {})}
        elif camera_from and camera_to:
            scene["camera"] = {"from": camera_from, "to": camera_to,
                               **({"fov": camera_fov} if camera_fov else {})}
        if background:
            scene["environment"] = {"background": background}
        self.scene_state = scene

    # ---- beat lifecycle ---------------------------------------------------

    def beat(self, id: str, narration: str = "", duration: float = 5.0) -> None:
        """Open a new beat. All subsequent action calls attach to it
        until the next beat() or finish()."""
        b = {"id": id, "narration": narration, "duration": float(duration), "actions": []}
        self.beats.append(b)
        self._current_beat = b

    def _add_action(self, action: dict) -> None:
        if self._current_beat is None:
            raise DirectorError("Action called before any beat() — open a beat first.")
        # Resolve aliases inside any part-ref field so the runtime sees real ids
        for field in ("target", "from", "to", "except"):
            if field in action:
                action[field] = self._resolve(action[field])
        self._current_beat["actions"].append(action)

    # ---- action constructors (all return None; side-effect on current beat) ----

    def highlight(self, target, color: str = "#5046E5", intensity: float = 1.0,
                  start_at: float = 0) -> None:
        self._check_part_ref(target)
        self._add_action({"type": "highlight", "target": target,
                          "color": color, "intensity": intensity, "startAt": start_at})

    def dim_others(self, except_: list | None = None, factor: float = 0.45,
                   start_at: float = 0) -> None:
        if except_: self._check_part_ref(except_)
        # Clamp on the schema side too — if the director asks for an extreme
        # value, snap it into a survivable range. Reduces the runtime's
        # defensive burden and keeps the contract JSON honest.
        factor = max(0.40, min(1.0, factor))
        self._add_action({"type": "dim_others", "except": except_ or [],
                          "factor": factor, "startAt": start_at})

    def hide(self, target, start_at: float = 0) -> None:
        self._check_part_ref(target)
        self._add_action({"type": "hide", "target": target, "startAt": start_at})

    def show(self, target, start_at: float = 0) -> None:
        self._check_part_ref(target)
        self._add_action({"type": "show", "target": target, "startAt": start_at})

    def show_only(self, target, start_at: float = 0) -> None:
        """Hide every part except the ones in `target`. Reliable way to expose
        internals — `hide(exterior_shell)` misses too many parts on this CAD."""
        self._check_part_ref(target)
        self._add_action({"type": "show_only", "target": target, "startAt": start_at})

    def play_animation(self, name: str, from_: float = 0, to: float = 1,
                       rate: float = 1.0, loop: bool = False, start_at: float = 0) -> None:
        self._check_anim_ref(name)
        self._add_action({"type": "play_animation", "animation": name,
                          "from": from_, "to": to, "rate": rate, "loop": loop,
                          "startAt": start_at})

    def bake_animation(self, name: str, parts: list, motion: str,
                       axis: list = None, magnitude_m: float = 0.04,
                       cycles_per_loop: int = 1, frames: int = 120,
                       start_at: float = 0) -> None:
        """Declare a NEW animation clip that doesn't exist yet in the asset's
        library. The orchestrator intercepts this action BEFORE shipping the
        contract: it calls /api/bake which runs Blender in --background mode
        to bake the requested motion onto the named parts, re-exports the
        GLB, and only then ships the contract (with the new clip name now
        valid in the runtime's animation library).

        motion: one of "reciprocate" (along axis) | "orbit" (around axis) |
                "spin" (around axis, same as orbit but treats parts as one rigid group)

        axis: world-space unit vector (e.g. [0,1,0] for the fan's airflow axis).
              Default: read from the asset's registry.rotation_axis_world.

        magnitude_m: peak-to-peak distance for reciprocate, or unused for
                     orbit/spin (uses the parts' radius from the axis).

        This is a one-shot escape hatch — prefer existing clips from the
        animation library when they suffice. Use this when the user asks
        about a motion no existing clip depicts."""
        self._add_action({
            "type": "bake_animation",
            "animation": name,
            "parts": parts,
            "motion": motion,
            "axis": axis,
            "magnitude_m": magnitude_m,
            "cycles_per_loop": cycles_per_loop,
            "frames": frames,
            "startAt": start_at,
        })

    def frame_on(self, target, margin: float = 1.8, ease: str = "easeInOut",
                 duration: float = 0.9, start_at: float = 0) -> None:
        """Auto-position the camera to frame the named part(s) with margin.
        Use this after `show_only` so the camera actually zooms to what's
        now visible. Much more reliable than guessing coordinates."""
        self._check_part_ref(target)
        self._add_action({"type": "frame_on", "target": target, "margin": margin,
                          "ease": ease, "duration": duration, "startAt": start_at})

    def move_camera(self, to_pose=None, *, to_preset: str | None = None,
                    preset: str | None = None, target: str | None = None,
                    to_from: list | None = None, to_to: list | None = None,
                    fov: float | None = None, ease: str = "easeInOut",
                    duration: float = 0.9, start_at: float = 0) -> None:
        """Move the camera. Accepted forms (any of):
            move_camera("hero_threequarter")            # positional preset
            move_camera(preset="hero_threequarter")     # without to_ prefix
            move_camera(to_preset="hero_threequarter")  # canonical
            move_camera(target="piston_1A")             # frame on a part (uses cylinder_close)
            move_camera(to_from=[x,y,z], to_to=[x,y,z], fov=30)
            move_camera({"preset": "..."})  or  ({"from":[...], "to":[...], "fov":...})
        """
        # Positional pose → either dict or preset string
        if to_pose is not None:
            if isinstance(to_pose, dict):
                to_preset = to_preset or to_pose.get("preset")
                to_from   = to_from   or to_pose.get("from")
                to_to     = to_to     or to_pose.get("to")
                fov       = fov       or to_pose.get("fov")
            elif isinstance(to_pose, str):
                to_preset = to_preset or to_pose
        # Synonym: bare `preset=`
        to_preset = to_preset or preset

        # target= → frame the named part using the close-up preset
        if target:
            self._check_part_ref(target)
            to_preset = to_preset or "cylinder_close"

        if to_preset:
            pose = {"preset": to_preset}
            if fov: pose["fov"] = fov
        elif to_from and to_to:
            pose = {"from": to_from, "to": to_to, **({"fov": fov} if fov else {})}
        else:
            # Permissive fallback: no args given — assume the director means
            # "go back to the wide hero shot". This is almost always what an
            # opening or summary beat wants.
            pose = {"preset": "hero_threequarter"}
        self._add_action({"type": "move_camera", "to": pose, "ease": ease,
                          "duration": duration, "startAt": start_at})

    def label(self, target: str, text: str, kicker: str | None = None,
              anchor: str = "auto", start_at: float = 0) -> None:
        self._check_part_ref(target)
        if len(text) > 80:
            raise DirectorError(f"label text too long ({len(text)} chars, max 80)")
        action = {"type": "label", "target": target, "text": text, "anchor": anchor,
                  "startAt": start_at}
        if kicker: action["kicker"] = kicker
        self._add_action(action)

    def show_panel(self, component: str = "ExplanationCard",
                   anchor: str = "screen-top-right", start_at: float = 0,
                   **props) -> None:
        if component not in ("ExplanationCard", "Glossary", "MetricsCard"):
            raise DirectorError(f"Unknown panel component '{component}'")
        self._add_action({"type": "show_panel", "component": component,
                          "anchor": anchor, "props": props, "startAt": start_at})

    def hide_panel(self, component: str | None = None, start_at: float = 0) -> None:
        a = {"type": "hide_panel", "startAt": start_at}
        if component: a["component"] = component
        self._add_action(a)

    def arrow(self, from_=None, to=None, *, to_=None, color: str = "#5046E5",
              style: str = "solid", start_at: float = 0) -> None:
        # Accept both `to=` (natural) and `to_=` (old).
        to = to if to is not None else to_
        if from_ is None or to is None:
            raise DirectorError("arrow needs from_ and to (a part id or [x,y,z])")
        for end in (from_, to):
            if isinstance(end, str):
                self._check_part_ref(end)
        self._add_action({"type": "arrow", "from": from_, "to": to,
                          "color": color, "style": style, "startAt": start_at})

    def pulse(self, target: str, cycles: int = 1, start_at: float = 0) -> None:
        self._check_part_ref(target)
        self._add_action({"type": "pulse", "target": target, "cycles": cycles,
                          "startAt": start_at})

    def reset(self, scope: str = "highlights", start_at: float = 0) -> None:
        if scope not in ("highlights", "visibility", "camera", "all"):
            raise DirectorError(f"reset scope must be one of highlights/visibility/camera/all, got {scope}")
        self._add_action({"type": "reset", "scope": scope, "startAt": start_at})

    # ---- finalisation -----------------------------------------------------

    def finish(self, *, title: str, summary: str, contract_id: str | None = None) -> dict:
        if not self.beats:
            raise DirectorError("Cannot finish a contract with zero beats.")
        cid = contract_id or _slugify(self.prompt)[:80]
        return {
            "meta": {
                "id": cid,
                "prompt": self.prompt,
                "schemaVersion": "0.1.0",
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "generatedBy": "director-v1",
                "asset": "engine/v8_engine.glb",
                "estimatedDurationSec": sum(b.get("duration", 0) for b in self.beats),
            },
            "explanation": {"title": title, "summary": summary},
            "scene": self.scene_state,
            "beats": self.beats,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(s: str, max_len: int = 80) -> str:
    out = []
    prev_dash = False
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch); prev_dash = False
        elif not prev_dash:
            out.append("-"); prev_dash = True
    return "".join(out).strip("-")[:max_len] or "contract"


def _closest(needle: str, haystack: Iterable[str], top: int = 3) -> list[str]:
    """Lightweight 'did you mean': longest common prefix length, then alphabetical."""
    scored = []
    for h in haystack:
        common = 0
        for a, b in zip(needle, h):
            if a == b: common += 1
            else: break
        scored.append((-common, h))
    scored.sort()
    return [h for _, h in scored[:top]]
