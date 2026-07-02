"""Curate ~10 visually distinct hero parts from the full V8 registry.

Why this exists:
    The full part_registry.json has 673 loose-parts from the V8 OBJ. When we
    pass all of them to the director LLM:
      (a) we blow rate limits
      (b) the director picks "pistons" that are actually bolts/brackets
         because the classification is noisy
      (c) labels end up anchored to invisible 5mm parts

    The curated set picks the largest, most spatially-distinctive parts in
    each region of the engine. Each is something a user would recognize as
    a real engine component when looked at — and big enough to label/halo.

What it produces:
    - Adds a "hero_parts" section to part_registry.json
    - Updates aliases with friendly names matching typical V8 components
    - Leaves the full "parts" dict intact for runtime lookups

The director is taught (via prompt) to use ONLY hero_parts and aliases.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


REG_PATH = Path(r"C:\SPATAIL_MAX\engineexplainer\engine\part_registry.json")


def curate(reg: dict) -> dict:
    parts = reg["parts"]

    # Group parts by (region, role) and pick the largest in each bucket.
    by_bucket = defaultdict(list)
    for pid, p in parts.items():
        region = p.get("region", "unknown")
        role   = p.get("role", "unclassified")
        size   = p.get("size_m", 0.0)
        if size < 0.05:  # too small to label meaningfully
            continue
        by_bucket[(region, role)].append((size, pid, p))

    # Pick the top-1 (by size) in each bucket, then prune to a curated set.
    picks: dict[str, dict] = {}
    for (region, role), entries in by_bucket.items():
        entries.sort(reverse=True)
        size, pid, p = entries[0]
        picks[pid] = {**p, "_bucket": f"{region}/{role}"}

    # Friendly aliases mapping a recognizable component name to the best-fit
    # part id (largest part in the matching region). Tuned for a generic V8.
    region_to_alias = {
        # name              acceptable regions, preference order
        "fan_assembly":     [("front-mid", "front-bottom")],
        "front_pulleys":    [("front-bottom",)],
        "intake_top":       [("mid-top",)],
        "valve_cover_a":    [("mid-top",)],
        "valve_cover_b":    [("mid-top",)],
        "engine_block":     [("mid-mid",)],
        "exhaust_left":     [("mid-bottom",)],
        "exhaust_right":    [("mid-bottom",)],
        "rear_assembly":    [("rear-mid", "rear-top", "rear-bottom")],
        "oil_pan_area":     [("mid-bottom", "rear-bottom")],
    }

    used_pids = set()
    alias_map: dict[str, str] = {}

    # Sort all picks by size so largest-first wins the alias slots
    picks_sorted = sorted(picks.items(), key=lambda kv: -kv[1].get("size_m", 0))

    for alias_name, region_prefs in region_to_alias.items():
        for region_choices in region_prefs:
            if isinstance(region_choices, str):
                region_choices = (region_choices,)
            for region in region_choices:
                # Find the largest unused part in this region
                for pid, p in picks_sorted:
                    if pid in used_pids: continue
                    if p.get("region") != region: continue
                    alias_map[alias_name] = pid
                    used_pids.add(pid)
                    break
                if alias_name in alias_map:
                    break
            if alias_name in alias_map:
                break

    # Keep existing aliases too, if their target part exists
    legacy_aliases = reg.get("aliases", {}) or {}
    for k, v in legacy_aliases.items():
        if k not in alias_map and v in parts:
            alias_map[k] = v
            used_pids.add(v)

    # The "hero_parts" the director sees is the union of aliased targets
    # plus a handful more big classified ones for variety. Cap at ~14.
    hero_pids = set(alias_map.values())
    extras = [pid for pid, p in picks_sorted
              if pid not in hero_pids and p.get("size_m", 0) > 0.12]
    for pid in extras[:max(0, 14 - len(hero_pids))]:
        hero_pids.add(pid)

    hero_parts = {
        pid: {k: parts[pid][k] for k in ("role", "region", "world_position", "size_m") if k in parts[pid]}
        for pid in hero_pids
    }

    reg["aliases"] = alias_map
    reg["hero_parts"] = hero_parts
    reg["_curated"] = (
        "The 'hero_parts' map + 'aliases' are what the director should reference. "
        "Full 'parts' (673 entries) is retained only for runtime alias resolution."
    )
    return reg


def main():
    reg = json.loads(REG_PATH.read_text())
    reg = curate(reg)
    REG_PATH.write_text(json.dumps(reg, indent=2))
    print(f"hero_parts: {len(reg['hero_parts'])}")
    print(f"aliases:    {len(reg['aliases'])}")
    for alias, pid in reg["aliases"].items():
        p = reg["parts"][pid]
        print(f"  {alias:<18} → {pid:<22} region={p.get('region'):<14} size={p.get('size_m'):.3f}m")


if __name__ == "__main__":
    main()
