"""Second-pass curator: pull *real* pistons / rods / crank throws out of the
classifier noise and add a structured 'internals' alias set the director can
address.

Why:
    The first curator (curate_hero_parts.py) bucketed parts by spatial region
    and picked the largest in each bucket. That gave us the engine EXTERIOR —
    fan, pulleys, intake top, valve covers, block exterior. None of those let
    the director answer "how does a piston work" because no actual piston
    mesh was addressable.

    classify-engine identified 177 'pistons' and 23 'rods' but the long tail
    is noise (small bolts/washers misshapen as discs). The signal is in the
    HEAD of that list. Pistons in a V8 form a known spatial pattern: 4 along
    each of two banks, evenly spaced along the crank axis, similar size.
    That pattern is recoverable from the data even when individual labels
    are noisy.

Outputs:
    Updates engine/part_registry.json with new aliases:
      - piston_1A .. piston_4B    (cylinder, bank A/B)
      - rod_1A    .. rod_4B
      - crank_main, crank_throw_1 .. crank_throw_4
      - exterior_shell            (list of parts to hide to expose internals)

    Adds an 'internal_anatomy' section the director can reason over.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import median

REG_PATH = Path(r"C:\SPATAIL_MAX\engineexplainer\engine\part_registry.json")
CLS_PATH = Path(r"C:\SPATAIL_MAX\assets_processed\treated\v8_engine\v8_engine.classification.json")
TREAT_PATH = Path(r"C:\SPATAIL_MAX\assets_processed\treated\v8_engine\v8_engine.treatment.json")
OVERRIDES_PATH = Path(r"C:\SPATAIL_MAX\engineexplainer\engine\manual_overrides.json")

# Naming convention for the sanitized GLB names: spaces → underscores, dot dropped
def sanitize(name: str) -> str:
    return name.replace(" ", "_").replace(".", "")


def load_treatment_parts() -> dict:
    """Return treatment-stage part records keyed by sanitized id, with
    bbox + eigvals + classifier role attached when available."""
    treat = json.loads(TREAT_PATH.read_text())
    cls = json.loads(CLS_PATH.read_text())
    pivots = {p["name"]: p for p in treat["stages"]["5_pivots"]["parts"]}
    pg     = {p["name"]: p for p in treat["stages"]["4_principal_geometry"]["parts"]}
    cls_by_name = {p["name"]: p for p in cls.get("parts", [])}

    out = {}
    for raw_name, piv in pivots.items():
        sid = sanitize(raw_name)
        pg_rec = pg.get(raw_name, {})
        eigvals = pg_rec.get("eigvals", [0, 0, 0])
        bbox_c  = pg_rec.get("bbox_centre", [0, 0, 0])
        # Approximate extent from eigvals (variance → spread)
        extent = [(e * 12) ** 0.5 if e > 0 else 0 for e in eigvals]
        out[sid] = {
            "raw_name": raw_name,
            "role_guess": cls_by_name.get(raw_name, {}).get("role", "unclassified"),
            "shape": piv.get("shape_class", "unknown"),
            "world_centre": bbox_c,
            "principal_axis": pg_rec.get("principal_axis", [0, 0, 0]),
            "eig_sizes": extent,   # sorted desc by eigenvalue order
            "size": max(extent),
        }
    return out


def find_pistons(parts: dict) -> list[tuple[str, dict]]:
    """V8 pistons sit in two banks of 4 along the crank axis (X) with the
    bank offset on Y. The classifier already pre-tagged candidate disc-like
    parts; we filter the head of that list by spatial regularity."""
    # All classifier-labelled pistons
    candidates = [(pid, p) for pid, p in parts.items() if p["role_guess"] == "piston"]
    if not candidates:
        return []

    # Filter to mid-engine X range (skip parts near the very front/rear)
    bbox_x = sorted(p["world_centre"][0] for _, p in candidates)
    if not bbox_x: return []
    x_lo, x_hi = bbox_x[0], bbox_x[-1]
    mid_lo, mid_hi = x_lo + 0.05 * (x_hi - x_lo), x_hi - 0.05 * (x_hi - x_lo)

    # Filter to disc-ish parts of similar size (pistons should be ~uniform)
    sizes = sorted(p["size"] for _, p in candidates)
    target_size = median(sizes[: len(sizes) // 4])  # use small-end median (real pistons)

    keep = []
    for pid, p in candidates:
        cx, cy, cz = p["world_centre"]
        if not (mid_lo <= cx <= mid_hi): continue
        # Size within 60% of target
        if not (target_size * 0.5 <= p["size"] <= target_size * 1.8): continue
        keep.append((pid, p))

    # Bucket by bank: parts with cy > 0 are bank B, cy < 0 are bank A
    bank_a = sorted([(pid, p) for pid, p in keep if p["world_centre"][1] < 0], key=lambda x: x[1]["world_centre"][0])
    bank_b = sorted([(pid, p) for pid, p in keep if p["world_centre"][1] >= 0], key=lambda x: x[1]["world_centre"][0])

    # Sample 4 from each bank, spaced as evenly as possible
    def sample_n(seq, n):
        if len(seq) <= n: return list(seq)
        step = len(seq) / n
        return [seq[int(i * step)] for i in range(n)]

    selected_a = sample_n(bank_a, 4)
    selected_b = sample_n(bank_b, 4)

    return [(pid, {**p, "_cylinder": i + 1, "_bank": bank})
            for bank, lst in (("A", selected_a), ("B", selected_b))
            for i, (pid, p) in enumerate(lst)]


def find_rods(parts: dict, pistons: list, *, exclude_pids: set | None = None) -> list[tuple[str, dict]]:
    """Connecting rods: long rod-like parts roughly under each piston,
    extending toward the crank (low Z).

    Filters: must be classifier 'connecting_rod', NOT already-aliased as
    exterior, and within a plausible size range for a real conrod
    (~10-25 of the OBJ's units; way larger or smaller is almost certainly
    something else)."""
    exclude_pids = exclude_pids or set()
    cands = [
        (pid, p) for pid, p in parts.items()
        if p["role_guess"] == "connecting_rod"
        and pid not in exclude_pids
    ]
    rods = []
    used = set()
    for pid_p, pdata in pistons:
        cx, cy, cz = pdata["world_centre"]
        best = None; best_d = 1e9
        for pid_r, rdata in cands:
            if pid_r in used: continue
            rx, ry, rz = rdata["world_centre"]
            if rz >= cz: continue   # rod must be below piston
            d = (rx - cx) ** 2 + (ry - cy) ** 2 + (rz - cz) ** 2
            if d < best_d:
                best, best_d = (pid_r, rdata), d
        if best:
            rods.append((best[0], {**best[1], "_cylinder": pdata["_cylinder"], "_bank": pdata["_bank"]}))
            used.add(best[0])
    return rods


def find_throws(parts: dict) -> list[tuple[str, dict]]:
    """Crank throws: blob-class parts on the crank line (low Z, varying X).
    A V8 has 4 throws (paired pistons share one)."""
    cands = [(pid, p) for pid, p in parts.items() if p["role_guess"] == "crank_throw"]
    # Sort by Z (lowest = on the crank) and take the bottom slice
    by_z = sorted(cands, key=lambda kv: kv[1]["world_centre"][2])
    crank_z_max = by_z[0][1]["world_centre"][2] + 8 if by_z else 0   # ~8 units thickness
    on_crank = [(pid, p) for pid, p in by_z if p["world_centre"][2] <= crank_z_max]
    # Bucket by X and pick 4 evenly distributed
    on_crank.sort(key=lambda kv: kv[1]["world_centre"][0])
    if len(on_crank) <= 4: return on_crank
    step = len(on_crank) / 4
    return [on_crank[int(i * step)] for i in range(4)]


def find_exterior_shell(reg: dict) -> list[str]:
    """The current curated aliases for exterior components — these are
    what to hide when the director wants to expose the internal anatomy."""
    return [reg["aliases"][k] for k in (
        "engine_block", "intake_top", "valve_cover_a", "valve_cover_b",
        "exhaust_left", "exhaust_right"
    ) if k in reg.get("aliases", {})]


def _apply_overrides(reg: dict) -> dict | None:
    """If a manual_overrides.json exists, use the hand-verified part ids
    directly. The heuristic fallback only fires for missing categories."""
    if not OVERRIDES_PATH.exists():
        return None
    ov = json.loads(OVERRIDES_PATH.read_text())
    aliases = reg.get("aliases", {})
    # Drop stale internal aliases first
    for k in list(aliases.keys()):
        if k.startswith(("piston_", "rod_", "crank_throw_")):
            del aliases[k]

    sanitized_parts = set(reg.get("parts", {}).keys())
    def sanitize(n): return n.replace(" ", "_").replace(".", "")

    inserted = {"pistons": 0, "throws": 0, "rods": 0}
    for alias_name, raw_name in ov.get("pistons", {}).items():
        sid = sanitize(raw_name)
        if sid in sanitized_parts:
            aliases[alias_name] = sid; inserted["pistons"] += 1
    for alias_name, raw_name in ov.get("crank_throws", {}).items():
        sid = sanitize(raw_name)
        if sid in sanitized_parts:
            aliases[alias_name] = sid; inserted["throws"] += 1
    rods_section = ov.get("rods", {})
    if not rods_section.get("_disabled"):
        for alias_name, raw_name in rods_section.items():
            if alias_name.startswith("_"): continue
            sid = sanitize(raw_name)
            if sid in sanitized_parts:
                aliases[alias_name] = sid; inserted["rods"] += 1

    reg["aliases"] = aliases
    rod_alias_list = [k for k in aliases if k.startswith("rod_")]

    # Replace internal_anatomy with the verified set
    reg["internal_anatomy"] = {
        "pistons": [k for k in aliases if k.startswith("piston_")],
        "rods": rod_alias_list,
        "throws": [k for k in aliases if k.startswith("crank_throw_")],
        "rods_disabled_reason": rods_section.get("_reason") if rods_section.get("_disabled") else None,
        "exterior_shell": _find_exterior_shell_from_reg(reg),
        "_guidance": (
            "Verified by hand. The director can address pistons by alias "
            "(piston_1A..piston_4B), crank throws by alias (crank_throw_1..4), "
            "and the connecting rods are NOT modeled in this CAD - narrate "
            "around them ('the rod inside the block transmits the force...') "
            "without trying to highlight a rod mesh."
        ),
        "_verified_by_overrides": True,
    }

    # Refresh hero_parts to match
    hero_pids = set(aliases.values())
    reg["hero_parts"] = {
        pid: {k: reg["parts"][pid][k] for k in ("role", "region", "world_position", "size_m") if k in reg["parts"].get(pid, {})}
        for pid in hero_pids if pid in reg["parts"]
    }
    print(f"manual_overrides applied: {inserted}")
    return reg


def _find_exterior_shell_from_reg(reg: dict) -> list[str]:
    aliases = reg.get("aliases", {})
    return [aliases[k] for k in ("engine_block", "intake_top", "valve_cover_a", "valve_cover_b", "exhaust_left", "exhaust_right") if k in aliases]


def main():
    reg = json.loads(REG_PATH.read_text())

    # Try overrides first; if they cover what's needed, skip the heuristic search
    overridden = _apply_overrides(reg)
    if overridden is not None:
        REG_PATH.write_text(json.dumps(overridden, indent=2))
        anatomy = overridden["internal_anatomy"]
        print(f"\nUsing verified overrides:")
        print(f"  pistons: {len(anatomy['pistons'])}  rods: {len(anatomy['rods'])}  throws: {len(anatomy['throws'])}")
        print(f"  aliases total: {len(overridden['aliases'])}")
        print(f"  hero_parts total: {len(overridden['hero_parts'])}")
        return

    parts = load_treatment_parts()

    # Drop any internal aliases from a previous run so stale rod mappings
    # don't survive when the curator legitimately finds none this time.
    aliases_in = reg.get("aliases", {})
    for k in list(aliases_in.keys()):
        if k.startswith(("piston_", "rod_", "crank_throw_")):
            del aliases_in[k]
    reg["aliases"] = aliases_in

    pistons = find_pistons(parts)
    # Don't let rod-finder pick parts already aliased as exterior shell
    # (otherwise the largest "rod" is just the engine block itself).
    reserved = set(aliases_in.values())
    rods    = find_rods(parts, pistons, exclude_pids=reserved)
    throws  = find_throws(parts)
    exterior = find_exterior_shell(reg)

    # Write into aliases with semantic names
    aliases = reg.get("aliases", {})
    for pid, p in pistons:
        aliases[f"piston_{p['_cylinder']}{p['_bank']}"] = pid
    for pid, p in rods:
        aliases[f"rod_{p['_cylinder']}{p['_bank']}"] = pid
    for i, (pid, _) in enumerate(throws, start=1):
        aliases[f"crank_throw_{i}"] = pid

    # Hero parts: union of all aliased targets
    hero_pids = set(aliases.values())
    reg["hero_parts"] = {
        pid: {k: reg["parts"][pid][k] for k in ("role", "region", "world_position", "size_m") if k in reg["parts"].get(pid, {})}
        for pid in hero_pids if pid in reg["parts"]
    }

    reg["aliases"] = aliases
    reg["internal_anatomy"] = {
        "pistons":   [f"piston_{p['_cylinder']}{p['_bank']}" for _, p in pistons],
        "rods":      [f"rod_{p['_cylinder']}{p['_bank']}" for _, p in rods],
        "throws":    [f"crank_throw_{i}" for i in range(1, len(throws) + 1)],
        "exterior_shell": exterior,
        "_guidance": (
            "Pistons, rods, and crank throws are INSIDE the engine block. To make "
            "them visible to the user, the director must FIRST `ctx.hide(target=[<exterior_shell parts>])` "
            "to expose the internals. The exterior list is provided so the director "
            "can issue one hide call to reveal the anatomy."
        ),
    }

    REG_PATH.write_text(json.dumps(reg, indent=2))
    print(f"pistons: {len(pistons)}  rods: {len(rods)}  throws: {len(throws)}")
    print(f"aliases now: {len(aliases)}")
    print(f"hero_parts now: {len(reg['hero_parts'])}")
    print(f"exterior_shell: {len(exterior)} parts")
    print()
    print("internal aliases:")
    for k in sorted(aliases):
        if k.startswith(("piston_", "rod_", "crank_")):
            target = aliases[k]
            print(f"  {k:<18} -> {target}")


if __name__ == "__main__":
    main()
