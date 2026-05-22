# figma_tools (stub)

Placeholder for the Figma → Spatial bridge.

## What this folder will eventually contain

A Figma plugin that lets a designer lay out the spatial UI for a scene and
exports the layout straight into the `uiElements` array of the
`SpatialSceneContract.json`. The designer never edits JSON; the developer
never edits a design file.

Concretely:

- a Figma component library of approved spatial UI primitives (button,
  toggle, info card, label, callout, hotspot, list)
- a plugin (`figma-plugin/`) that reads the active page and emits valid
  `uiElement` objects bound to a scene id
- a CLI (`figma_sync.js`) that pulls the latest layout for a scene and
  merges it into the contract under `uiElements`, preserving the bindings
  to `interactionBricks`

## Why it's stubbed in v0.1

The contract schema is the prerequisite. v0.1 freezes:

- the shape of a `uiElement` (`id`, `type`, `label`, `action`, `target`,
  `visibleIn`)
- the dispatch contract: every UI element triggers an `interactionBrick`
  by id, never by inlining behavior

Once that's settled (it is, as of v0.1), the Figma side is decoupled work
and a v0.2 candidate.
