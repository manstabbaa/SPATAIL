# SPATAIL Placement Design System

> Authoritative spec, supplied by the product owner 2026-06-10.
> Executable encoding: `studio/spatail/design_system.py` (policy) feeding the
> on-device placement solver (`ios/.../PlacementSolver.swift`, geometry).
> The PLACED stream of every SPATAIL experience is governed by this document.

## Purpose

The SPATAIL Placement Design System defines how digital objects should be placed, sized,
oriented, and made interactive inside real physical space. It ensures that SPATAIL does
not simply "spawn a 3D model," but instead creates a spatial experience that feels
intentional, comfortable, readable, and useful.

Placement is based on: user intent · object type · real-world surface · physical scale ·
user distance · interaction needs · comfort · safety · readability.

# 1. Core Principle

## Placement is part of the meaning.

Where an object is placed changes what the object communicates.

- A skeleton placed life-size on the floor feels like a real body reference.
- A skeleton placed small on a table feels like a study model.
- A V8 engine on a table feels like an inspectable learning object.
- A V8 engine aligned to a real car feels like a repair guide.

SPATAIL should always ask:

> What is this object supposed to help the user understand or do?

# 2. Placement Decision Hierarchy

SPATAIL makes placement decisions in this order:

1. **User intent** — what is the user trying to do?
2. **Object category** — furniture, anatomy, mechanical, educational, architectural, vehicle-related, or abstract?
3. **Placement mode** — real-scale, miniature, exploded, overlaid, simulated, or compared?
4. **Anchor type** — floor, table, wall, room, object, hand, or user?
5. **Scale** — true size, shrink, enlarge, or adapt to the space?
6. **Position** — where relative to the user and the room?
7. **Orientation** — face the user, align to a wall, align to a real object, or follow the surface normal?
8. **Interaction** — rotate, scale, grab, explode, isolate parts, step through a process?
9. **Comfort** — readable, reachable, within a comfortable field of view?
10. **Fallback** — what happens if the ideal surface or space is not available?

# 3. Placement Modes

## 3.1 Real-Scale Placement
Use when the object needs to preserve real-world size.
Best for: furniture, appliances, TVs, human body models, vehicles, room planning, product preview.
Example: a chair appears at true physical scale on the floor so the user can judge fit, height, proportion.
Rules: preserve real-world dimensions; prefer floor or wall anchors; allow repositioning;
avoid scaling unless the real object does not fit; provide a miniature fallback if the space is too small.

## 3.2 Miniature Placement
Use when the real object or system is too large to show at full scale.
Best for: solar system, cities, bridges, engines, buildings, historical sites, large anatomy systems, geography models.
Example: a bridge appears as a miniature room-scale or tabletop model so the user can inspect the full structure.
Rules: scale down large systems; keep the model inspectable; show a scale reference;
allow rotate, zoom, isolate, annotation; make clear the object is not true scale.
Suggested UI copy: "Scaled to 1:20 for tabletop view."

## 3.3 Exploded View Placement
Use when the user needs to understand how parts fit together.
Best for: engines, machines, appliances, furniture assembly, electronics, mechanical systems.
Example: a V8 engine separates into meaningful part groups, not random disconnected pieces.
Rules: explode parts along readable axes; keep the full structure understandable; allow
collapse back to original form; let users isolate individual parts; add labels only when
needed; do not overload the scene with every label at once.
Default interactions: explode · collapse · isolate part · highlight part · show function · step through assembly.

## 3.4 Object-Anchored Overlay
Use when SPATAIL needs to guide the user on a real object.
Best for: car repair, appliance repair, furniture assembly, mechanical guidance, maintenance instructions.
Example: for a Mustang air filter guide, SPATAIL highlights the real air filter box, places
arrows on the correct clips, and keeps the instruction panel beside the engine bay.
Rules: preserve true scale; align directly to the real-world object; do not cover the
target area; place instructions beside or above the task zone; use highlights, ghost
parts, arrows, step cards; pause if tracking is lost.
Default interactions: next step · confirm step · highlight current part · show tool · show ghost replacement · re-align overlay.

## 3.5 Wall Placement
Use when the content behaves like a spatial board, poster, diagram, or vertical explanation.
Best for: educational diagrams, timelines, anatomy boards, PDF explanations, posters, instructions, visual references.
Example: a photosynthesis diagram appears on a wall like a spatial whiteboard, with expandable parts and readable labels.
Rules: align flush to the wall; place around eye level; keep text flat and readable; use
progressive labels; allow expand, annotate, pin; if no wall exists, fall back to a floating panel.

## 3.6 Room-Scale Simulation
Use when the room becomes the stage for a process or system.
Best for: gravity simulation, bridge planning, traffic flow, interior design, architecture, environmental systems, physics simulations.
Example: a gravity demo places a tree and apple in the room, with force arrows, motion paths, and a timeline scrubber.
Rules: use available floor and room space; keep controls accessible; avoid placing objects
behind the user; allow walking around when useful; use timeline controls for processes;
keep the main action inside the user's comfortable view.

# 4. Anchor Types

## Floor Anchor
Use for: large objects, furniture, real-scale previews, human body models, room-scale scenes, vehicles.
Rules: object sits on the floor plane; preserve gravity and grounding; use shadows when
possible; leave walkable space around the object.

## Table Anchor
Use for: small objects, educational models, mechanical models, miniature scenes, inspectable systems.
Rules: center the object on the table; fit within the usable table area; keep interaction
within arm's reach; allow rotate, inspect, isolate, scale.

## Wall Anchor
Use for: diagrams, panels, posters, instructions, timelines, wall-mounted objects.
Rules: align flat to the wall; center around eye level; keep text readable; avoid placing
content too high, low, or far to the side.

## Object Anchor
Use for: repair overlays, object recognition, assembly guidance, real-world alignment.
Rules: attach content to the detected object; preserve real scale; use highlights and
ghost geometry; avoid covering the physical object; re-align if tracking confidence drops.

## Room Anchor
Use for: full-room layouts, architecture, simulation, interior design, navigation, environmental visualization.
Rules: understand room boundaries; respect walls, floor, obstacles; keep main content in
front of the user; avoid blocking walkways.

## User-Relative Anchor
Use for: menus, tool palettes, temporary controls, assistant panels, reset buttons.
Rules: keep lightweight UI near the user; do not lock large objects to the user; avoid
attaching important 3D content to the camera; use for controls, not the main scene.

# 5. Scale Rules

## True Scale
The object appears at its actual real-world size.
Use for: furniture, appliances, TVs, human body, car parts, product previews.
Rule: use true scale when the user needs to judge real-world fit, size, or proportion.

## Fit-to-Surface
The object scales to fit the available surface.
Use for: tabletop learning objects, wall diagrams, miniature models, educational displays.
Rule: the object should fit comfortably within the surface without feeling cramped.
Suggested limit: use no more than 60–70% of the available surface width.

## Miniature Scale
The object is intentionally reduced.
Use for: buildings, bridges, solar system, engines, city layouts, large systems.
Rule: miniature objects must include a scale reference or scale note.

## Enlarged Detail
The object is intentionally enlarged so the user can inspect details.
Use for: cells, molecules, small car parts, valves, bolts, circuits.
Rule: tiny objects should become hand-inspectable, but SPATAIL should communicate that they are enlarged.

## Adaptive Scale
The object changes size based on room size, surface size, or user intent.
Use for: unknown room sizes, large assets, educational simulations, product previews with limited space.
Rule: adaptive scaling should preserve usability first and realism second.

# 6. Position Rules

## General
- Place main content in front of the user; never spawn behind the user.
- Avoid placing objects too close to the camera.
- Avoid placements that force head-turning.
- Keep interactable objects within comfortable reach; keep large objects far enough to view properly.
- Respect real-world surfaces and room boundaries; leave space to walk around when needed.

## Table position
Center on the detected table · face the user · fit within the table bounds · controls
beside the object, not on top · leave margins. Best for learning, inspection, small
simulations, mechanical models.

## Floor position
In front of the user · grounded naturally · preserve walk-around space · avoid clipping
through walls or furniture · true scale when appropriate. Best for furniture, anatomy,
large objects, room-scale scenes.

## Wall position
Align to the wall plane · around eye level · readable text · avoid corners unless
intentional · within the comfortable field of view. Best for diagrams, guides, boards.

## Object overlay position
Attach highlights to the real object · offset instruction panels from the target · keep
the target visible · arrows and outlines sparingly · current step visually dominant.
Best for repair, assembly, maintenance, diagnostics.

# 7. Comfort Rules

## Distance Zones

| Zone      | Distance  | Best For                                  |
|-----------|-----------|-------------------------------------------|
| Near Zone | 0.3–0.8 m | Grabbing, rotating, inspecting             |
| Work Zone | 0.8–1.5 m | Tabletop learning, repair guides           |
| View Zone | 1.5–3 m   | Large objects, wall content, comparison    |
| Room Zone | 3 m+      | Architecture, vehicles, room simulations   |

## Rules
No forced constant head turning · no important content too high/low · readable text ·
controls close to where the user looks · recentering when placement feels awkward ·
direct manipulation only when close enough · indirect selection for farther objects.

# 8. Interaction Rules

## Basic interactions (simple models)
move · rotate · scale · reset · delete · reposition

## Semantic interactions (SPATAIL experiences)
explode · collapse · isolate part · highlight part · show function · cutaway · animate
process · step forward · step backward · confirm step · compare · measure

Important rule:
> Do not expose generic interactions when a semantic interaction would be clearer.

Bad: "Tap part". Better: "Isolate piston" · "Show airflow path" · "Highlight air filter clips" · "Collapse engine view".

# 9. Spatial UI Rules

## Text
Mostly flat · facing the user · no unnecessary 3D text · short labels · larger panels for
explanations · no dense text inside the 3D object.

## Labels
Appear on selection when possible · never overlap · leader lines when needed · readable
from the user's position · collapse when crowded · expand progressively.

## Panels
Place beside the object, above the object, pinned to a wall, or user-relative only when
temporary. Avoid: directly over the main object, behind the object, too far from the
action, too close to the user's face.

# 10. Fallback Rules

- **No table detected** → 1) floor placement 2) user-relative miniature volume 3) ask to scan again.
- **No wall detected** → 1) floating panel 2) floor stand 3) tabletop version.
- **Room too small** → 1) miniature mode 2) fit-to-surface mode 3) 2D preview mode.
- **Tracking confidence low** → 1) show scan guidance 2) pause placement 3) ask to re-align 4) reduce interaction complexity.
- **Object too large** → 1) miniature preview 2) true-scale toggle 3) partial view 4) room-scale warning.
  Example message: "This object is too large for the current room. Showing a scaled preview instead."

# 11. Presets

## Preset: Tabletop Learning Model
For solar system, cell biology, engine model, architecture model, historical structure, mechanical explanation.
Anchor to table · fit within 60–70% of surface width · face the user · rotate/scale/isolate/animate · progressive labels · controls beside the model.
Default interactions: rotate · scale · isolate · highlight · animate · reset.

## Preset: True-Scale Product Preview
For furniture, TVs, appliances, room objects, product comparison.
True scale · anchor to floor or wall · allow repositioning · preserve real dimensions ·
shadows for grounding · miniature fallback if it does not fit.
Default interactions: move · rotate · measure · compare · reset.

## Preset: Mechanical Exploded View
For engines, gearboxes, machines, electronics, furniture assemblies.
Table if possible, floor if unavailable · explode along readable axes · clear part
hierarchy · collapse back to original · labels only on selected parts.
Default interactions: explode · collapse · isolate · highlight · cutaway · step sequence.

## Preset: Object-Anchored Repair Guide
For car repair, appliance repair, furniture assembly, maintenance, installation.
Anchor to the real object · true scale · keep target visible · offset instruction panels ·
highlight only the current step · pause if tracking is lost.
Default interactions: next step · previous step · confirm step · highlight target · show tool · show ghost part · re-align.

## Preset: Wall Explanation Board
For diagrams, timelines, PDF explanations, educational boards, visual breakdowns.
Anchor to wall, flat · around eye level · readable text · expandable sections · floating
panel fallback if no wall.
Default interactions: expand · collapse · annotate · pin · step through.

## Preset: Room-Scale Simulation
For physics, architecture, bridge planning, interior design, environmental systems, spatial processes.
The room is the stage · main action in front of the user · walkable space · timeline or
parameter controls · avoid crowding · allow recentering.
Default interactions: play · pause · scrub timeline · change parameter · walk around · reset.

# 12. Placement Contract Format

Every SPATAIL scene outputs a placement contract:

```json
{
  "intent": "mechanical_exploded_view",
  "anchor": { "type": "table", "fallback": "floor" },
  "scale": {
    "mode": "fit_to_surface",
    "maxSurfaceCoverage": 0.65,
    "showScaleReference": true
  },
  "position": {
    "alignment": "centered_on_surface",
    "faceUser": true,
    "avoidOcclusion": true
  },
  "interaction": {
    "canMove": true, "canRotate": true, "canScale": true,
    "semanticActions": ["explode", "collapse", "isolate_part",
                        "highlight_part", "cutaway", "reset"]
  },
  "ui": {
    "labelBehavior": "on_select",
    "panelPosition": "beside_object",
    "textMode": "flat_user_facing"
  },
  "comfort": {
    "preferredDistance": "0.8m-1.5m",
    "avoidForcedHeadTurn": true,
    "allowRecentering": true
  },
  "fallback": {
    "ifNoTable": "use_floor",
    "ifRoomTooSmall": "miniature_mode",
    "ifTrackingLow": "request_rescan"
  }
}
```

# 13. Summary

SPATAIL's Placement Design System converts user intent, object type, room context,
surface detection, scale requirements, and interaction needs into consistent AR placement
behavior. The system decides: where the object goes · what it anchors to · how big it is ·
which direction it faces · how close it is to the user · what interactions it supports ·
where labels and controls appear · how it adapts when the room is not ideal.

# 14. Core Rules Cheat Sheet

**Placement** — by intent, not file type · prefer real-world anchors over floating ·
main content in front of the user · never block the real object the user needs · always
provide a fallback.
**Scale** — true scale when fit matters · miniature when too large · enlarged when
details are too small · always communicate changed scale.
**Interaction** — match the task · semantic over generic · direct manipulation only when
close · step-based controls for repair and education.
**UI** — readable, mostly flat text · progressive labels · panels beside the action · no
3D-scene clutter.
**Comfort** — no forced head turning · no awkward reach · controls near the object ·
allow recentering · respect the room size.

# 15. One-Sentence Product Rule

> SPATAIL places digital objects according to what the user is trying to understand or
> do, using real-world surfaces, correct scale, comfortable positioning, and meaningful
> interactions to turn 3D content into useful spatial experiences.
