"""
spatail_authoring_classifier.py — name/group → material_class + rig_kind.

Pure Python (no Blender imports). Importable from any authoring script
or from a future cloud worker. The classifier is intentionally a tiny
keyword matcher — rough capability wins over depth, per the product
brief. Manual overrides via `spatail_rig_kind` and `spatail_material`
custom properties on the Blender object always win.

Returns one of:
    MATERIAL_CLASSES (closed enum, mirrored in the materials library)
    RIG_KINDS        (closed enum, mirrored in the rigs library)

Keep both lists in lockstep with assets_authoring/{materials,rigs}/.
"""

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Closed vocabularies. New value = new node group in the library + new
# entry in the keyword table below.
# ---------------------------------------------------------------------------

MATERIAL_CLASSES = [
    "metal_polished",      # chrome, quick-release nuts, badges
    "metal_brushed",       # rim bars, machined surfaces
    "plastic_matte",       # housings, knobs, control bays
    "plastic_soft_touch",  # grips, dampers
    "rubber",              # tires, seals, gaskets
    "glass_tinted",        # displays, lenses
    "display_emissive",    # LCD screens, indicators
    "paint_clearcoat",     # body panels, hero accent surfaces
    "wood",                # tables, props
    "fabric",              # sofa, seats
    "placeholder_neutral", # fallback
]

RIG_KINDS = [
    "transform_only",   # single rigid object
    "parented_group",   # rigid parts moving as a hierarchy
    "constraint_rig",   # Blender constraints handle the motion
    "armature_fk",      # bones + FK
    "armature_ik",      # bones + IK
    "shape_keys",       # vertex-blend morphs
    "simulation_cache", # cloth/rigid body/soft body baked
]


# Keyword → class. First match wins. Tokens are matched case-insensitive
# against a haystack made of the object name + source group + any tags.
MATERIAL_KEYWORDS = [
    ("metal_polished",     ["chrome", "polished", "badge", "star", "quick_release",
                            "nut", "bezel", "mirror"]),
    ("metal_brushed",      ["brushed", "rim_bar", "machined", "aluminium", "aluminum",
                            "steel", "rim"]),
    ("plastic_soft_touch", ["grip", "handle", "wrap", "leather", "suede", "alcantara",
                            "damper", "armrest"]),
    ("rubber",             ["tire", "tyre", "seal", "gasket", "boot", "bushing"]),
    ("glass_tinted",       ["lens", "lcd_glass", "screen_glass", "windshield",
                            "window_glass"]),
    ("display_emissive",   ["display", "screen", "lcd", "indicator", "led", "console_display",
                            "center_console"]),
    ("paint_clearcoat",    ["body_panel", "hood", "door_panel", "fender", "hero_accent",
                            "paint"]),
    ("wood",               ["wood", "oak", "walnut", "table_top", "desk_top"]),
    ("fabric",             ["fabric", "seat_cushion", "sofa", "upholstery", "carpet"]),
    ("plastic_matte",      ["plastic", "housing", "bay", "button", "switch", "console",
                            "shroud", "spoke", "trim", "hub", "knob"]),
]

# Semantic-role → rig kind. The bootstrap looks at the assetGroupRef +
# the object's role custom property + name tokens. Order matters: the
# "singular part" matchers come FIRST so a thing called "Left Grip Handle"
# inside the group "steering_wheel" doesn't get classified as a
# parented_group on the strength of the group token alone.
RIG_KEYWORDS = [
    ("transform_only",   ["callout", "panel", "marker", "badge", "lens",
                          "grip", "handle", "star", "rim_bar", "console",
                          "bay", "button", "spoke", "hub", "knob"]),
    ("shape_keys",       ["morph", "soft_touch_squish", "filter_dirty"]),
    ("simulation_cache", ["cloth", "drape", "fluid", "softbody"]),
    ("armature_ik",      ["claw", "gripper", "linkage_ik", "suspension"]),
    ("armature_fk",      ["linkage", "scissor", "robot_arm_fk"]),
    ("constraint_rig",   ["follow_path", "track", "lookat_constraint"]),
    ("parented_group",   ["assembly", "exploded", "explode", "rotor", "manifold"]),
]


def _normalize(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def _haystack(name: str, group: Optional[str] = None, tags: Optional[list] = None) -> str:
    parts = [_normalize(name), _normalize(group)]
    for t in tags or []:
        parts.append(_normalize(t))
    return "_".join(p for p in parts if p)


def classify_material(name: str,
                       group: Optional[str] = None,
                       tags: Optional[list] = None,
                       override: Optional[str] = None) -> str:
    """Return a MATERIAL_CLASSES value. Override wins; otherwise keyword match;
    otherwise placeholder_neutral."""
    if override and override in MATERIAL_CLASSES:
        return override
    hay = _haystack(name, group, tags)
    for cls, words in MATERIAL_KEYWORDS:
        for w in words:
            if w in hay:
                return cls
    # Soft fallback: anything that sounds vaguely housing-shaped gets
    # plastic_matte so the viewer doesn't end up grey-on-grey.
    if any(w in hay for w in ("assembly", "body", "frame", "casing", "shell")):
        return "plastic_matte"
    return "placeholder_neutral"


def classify_rig(name: str,
                 group: Optional[str] = None,
                 tags: Optional[list] = None,
                 override: Optional[str] = None) -> str:
    """Return a RIG_KINDS value. Override wins; keyword match; transform_only."""
    if override and override in RIG_KINDS:
        return override
    hay = _haystack(name, group, tags)
    for kind, words in RIG_KEYWORDS:
        for w in words:
            if w in hay:
                return kind
    return "transform_only"


# Convenience: classify all of an object's relevant fields at once. The
# Blender-side wrapper passes this dict into the materials / rig wiring.
def classify(name: str,
             group: Optional[str] = None,
             tags: Optional[list] = None,
             material_override: Optional[str] = None,
             rig_override: Optional[str] = None) -> dict:
    return {
        "material": classify_material(name, group, tags, material_override),
        "rig":      classify_rig(name, group, tags, rig_override),
        "_haystack": _haystack(name, group, tags),
    }


if __name__ == "__main__":
    # Tiny self-test runnable outside Blender.
    cases = [
        ("Left Grip Handle", "steering_wheel", None,
         {"material": "plastic_soft_touch", "rig": "transform_only"}),
        ("Mercedes Star", "steering_wheel", None,
         {"material": "metal_polished", "rig": "transform_only"}),
        ("Lower Rim Bar", "steering_wheel", None,
         {"material": "metal_brushed", "rig": "transform_only"}),
        ("Center Console Display", "steering_wheel", None,
         {"material": "display_emissive", "rig": "transform_only"}),
        ("Top-Left Control Bay", "steering_wheel", None,
         {"material": "plastic_matte", "rig": "transform_only"}),
        ("Steering Wheel Assembly", "steering_wheel", None,
         {"material": "plastic_matte", "rig": "parented_group"}),
        ("Hood Panel", "car_body", None,
         {"material": "paint_clearcoat", "rig": "transform_only"}),
        ("Front Tire", "wheels", None,
         {"material": "rubber", "rig": "transform_only"}),
        ("Front Wheel Assembly", "wheels", None,
         {"material": "plastic_matte", "rig": "parented_group"}),
    ]
    fails = 0
    for name, group, tags, want in cases:
        got = classify(name, group, tags)
        ok = got["material"] == want["material"] and got["rig"] == want["rig"]
        marker = " " if ok else "X"
        if not ok:
            fails += 1
        print(f"[{marker}] {name:30s} group={group:20s} -> "
              f"material={got['material']:20s} rig={got['rig']:15s} "
              f"(want material={want['material']}, rig={want['rig']})")
    if fails:
        raise SystemExit(f"{fails} classifier case(s) failed")
    print("classifier: all cases passed")
