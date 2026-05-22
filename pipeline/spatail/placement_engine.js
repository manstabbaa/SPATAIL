// SpatialPlacementEngine
//
// Given an element with a chosen representation mode + the running scene
// layout, decide where it physically lives:
//   - placement.kind (wall / table / floor / object_anchored / above_target /
//     near_user / near_presenter / left_of_user / right_of_user /
//     in_front_of_user / room_center) — the closed vocab
//   - anchorStrategy (world / plane / object / relative_to_target /
//     user_relative / simulated)
//   - scaleMode (real / tabletop / enlarged_detail / compact_panel / room)
//   - position (x, y, z in metres — viewer renders it literally; visionOS
//     overrides at runtime based on the real room)
//
// Card-level overrides:
//   A source can carry `placementHint` (one of the placement vocab values).
//   The engine respects the hint when it makes sense for the chosen mode.

const ROOM = {
  widthX: 6,
  depthZ: 4,
  wallY: 1.6,        // eye-height for wall-mounted panels
  tableHeight: 0.75,
  // User stands at (0, 0, 1.7); +Z is toward the camera, -Z is the back wall.
  userZ: 1.7,
  userEyeY: 1.6,
};

export function createLayoutState() {
  return {
    // Per-side stack cursors so successive panels don't overlap.
    left_of_user:  { nextY: 1.5, nextStack: 0 },
    right_of_user: { nextY: 1.5, nextStack: 0 },
    wall:          { nextX: -1.2, nextY: 2.0 },
    floor:         { nextX: -1.5 },
    near_user:     { nextX: -0.6 },
    in_front_of_user: { nextX: 0 },
    counters: {},
  };
}

export function placeElement(element, layout, ctx) {
  const { representationMode } = element;
  const hint = element.sourceContent?.placementHint;

  switch (representationMode) {
    case "two_d_panel":
      return panelByHint(element, layout, ctx, hint);
    case "wall_dashboard":
      return wallDashboard(element, layout);
    case "tabletop_model":
    case "three_d_model":
      return tabletop(element, layout);
    case "floor_timeline":
      return floorTimeline(element, layout);
    case "floating_decision_card":
      return decisionCard(element, layout);
    case "highlighted_target":
      return physicalTarget(element, layout, ctx);
    case "exploded_view":
      return explodedAboveTarget(element, layout, ctx);
    case "anchored_callout":
      return anchoredCallout(element, layout, ctx);
    case "diagnostic_overlay":
      return diagnosticOverlay(element, layout, ctx);
    case "guide_line":
      return guideLine(element, layout, ctx);
    case "airflow_field":
      return airflowField(element, layout, ctx);
    default:
      return inFrontOfUser(element, layout);
  }
}

function airflowField(element, layout, ctx) {
  // Streamlines anchor to a hero (the F1 car). Position is the hero's
  // centroid; the renderer uses the source's `streams` to draw the lines
  // from the hero outwards. No layout cursor needed — multiple regimes
  // can co-exist (the renderer toggles by regime).
  const targetId = element._relatesToTargetElementId;
  const target = targetId ? ctx.elementsById.get(targetId) : null;
  const [tx, ty, tz] = target?.placement?.position || [0, 0.75, 0];
  return {
    placement: {
      kind: "object_anchored",
      anchor: `airflow_around:${target?.id || "unknown"}`,
      position: [tx, ty, tz],
      sizeMeters: [1.0, 1.0, 2.0],
    },
    anchorStrategy: "relative_to_target",
    scaleMode: "real_scale",
    whyPlacement:
      "Airflow only makes sense relative to the body it flows around — " +
      "anchored to the hero geometry so streamlines stay registered to the car.",
  };
}

// --------------------------------------------------------------------------
// Per-mode placement strategies
// --------------------------------------------------------------------------

function panelByHint(element, layout, ctx, hint) {
  // Honor an explicit hint first.
  if (hint === "right_of_user") return panelRightOfUser(element, layout);
  if (hint === "left_of_user")  return panelLeftOfUser(element, layout);
  if (hint === "wall")          return wallDashboard(element, layout);

  // Step sequences default to right_of_user so the user can keep them in
  // view while bent over the target. Everything else (status, insurance,
  // history) goes left_of_user as persistent reference.
  if (element.contentType === "step_sequence") {
    return panelRightOfUser(element, layout);
  }
  return panelLeftOfUser(element, layout);
}

function panelLeftOfUser(element, layout) {
  const slot = layout.left_of_user;
  const x = -ROOM.widthX / 2 + 0.6;
  const y = slot.nextY;
  const z = ROOM.userZ - 0.4 - slot.nextStack * 0.05;
  slot.nextY -= 0.95;
  slot.nextStack += 1;
  if (slot.nextY < 0.6) { slot.nextY = 1.5; }
  return {
    placement: {
      kind: "left_of_user",
      anchor: "left_of_user",
      position: [x, y, z],
      rotationDeg: [0, 55, 0],
      sizeMeters: [1.0, 0.8],
    },
    anchorStrategy: "user_relative",
    scaleMode: "compact_panel",
    whyPlacement:
      "Persistent reference content (status, insurance, history) belongs to " +
      "the user's left so it stays glanceable without competing with the " +
      "repair target straight ahead.",
  };
}

function panelRightOfUser(element, layout) {
  const slot = layout.right_of_user;
  const x = ROOM.widthX / 2 - 0.6;
  const y = slot.nextY;
  const z = ROOM.userZ - 0.4 - slot.nextStack * 0.05;
  slot.nextY -= 0.95;
  slot.nextStack += 1;
  if (slot.nextY < 0.6) { slot.nextY = 1.5; }
  return {
    placement: {
      kind: "right_of_user",
      anchor: "right_of_user",
      position: [x, y, z],
      rotationDeg: [0, -55, 0],
      sizeMeters: [1.0, 0.8],
    },
    anchorStrategy: "user_relative",
    scaleMode: "compact_panel",
    whyPlacement:
      "Active instructions (steps, tools list) live to the user's right — " +
      "the dominant working side — so they stay in peripheral view while " +
      "the user looks at the target.",
  };
}

function wallDashboard(element, layout) {
  const slot = layout.wall;
  const pos = [slot.nextX, slot.nextY, -ROOM.depthZ / 2 + 0.02];
  slot.nextX += 1.4;
  if (slot.nextX > 1.6) { slot.nextX = -1.2; slot.nextY -= 1.05; }
  return {
    placement: {
      kind: "wall",
      anchor: "wall_back",
      position: pos,
      rotationDeg: [0, 0, 0],
      sizeMeters: [1.3, 0.95],
    },
    anchorStrategy: "plane_anchor",
    scaleMode: "enlarged_detail",
    whyPlacement:
      "Dashboards are shared overview information — the back wall is the " +
      "obvious 'everyone looks here' surface for glanceable KPIs.",
  };
}

function tabletop(element, layout) {
  const n = bump(layout.counters, "tabletop");
  const pos = [n === 0 ? 0 : (n % 2 ? 0.7 : -0.7), ROOM.tableHeight, 0];
  return {
    placement: {
      kind: "table",
      anchor: "table_center",
      position: pos,
      sizeMeters: [0.8, 0.4, 0.8],
    },
    anchorStrategy: "plane_anchor",
    scaleMode: "tabletop_scale",
    whyPlacement:
      "Inspectable 3D systems belong on the table — the user walks around " +
      "them without them filling the room.",
  };
}

function floorTimeline(element, layout) {
  const slot = layout.floor;
  const steps = element.sourceContent?.steps
    || element.sourceContent?.events
    || [];
  const count = Array.isArray(steps) ? steps.length : 4;
  const pos = [slot.nextX, 0.01, 1.5];
  slot.nextX += Math.max(count * 0.45 + 0.5, 1.5);
  return {
    placement: {
      kind: "floor",
      anchor: "floor_front",
      position: pos,
      orientation: "user_forward",
      sizeMeters: [count * 0.45, 0.01, 0.4],
    },
    anchorStrategy: "plane_anchor",
    scaleMode: "room_scale",
    whyPlacement:
      "A timeline becomes physically walkable — every step is a stone on " +
      "the floor the user can step onto.",
  };
}

function decisionCard(element, layout) {
  const n = bump(layout.counters, "decision");
  const pos = [-0.6 + n * 0.5, 1.3, 1.2];
  return {
    placement: {
      kind: "near_user",
      anchor: "hand_reach",
      position: pos,
      sizeMeters: [0.45, 0.3],
    },
    anchorStrategy: "user_relative",
    scaleMode: "compact_panel",
    whyPlacement:
      "Decisions need to be touched / selected, so they live in the user's " +
      "hand-reach zone, not on a distant wall.",
  };
}

function physicalTarget(element, layout, ctx) {
  const hint = element.sourceContent?.placementHint;

  // Workbench / teardown flow: the hero object sits on a clean table at
  // the user's working height. There's no host vehicle / system to anchor
  // it to (cf. the Mustang engine-bay flow) — it's a Lego-manual subject.
  if (hint === "table") {
    const n = bump(layout.counters, "physical_target_table");
    const pos = [0, ROOM.tableHeight + 0.05, n === 0 ? 0 : 0.6];
    return {
      placement: {
        kind: "table",
        anchor: "table_center",
        position: pos,
        sizeMeters: [0.6, 0.3, 0.6],
      },
      anchorStrategy: "plane_anchor",
      scaleMode: "real_scale",
      whyPlacement:
        "Hero object sits on a clean workbench — no host vehicle to anchor " +
        "to, so the user can walk around the whole part for inspection.",
    };
  }

  // Default flow: the part is anchored to a simulated host (engine bay /
  // dashboard / etc.) so the highlight points at a real, physical spot.
  const n = bump(layout.counters, "physical_target");
  const pos = [0, ROOM.tableHeight + 0.05, n === 0 ? 0 : 0.6];
  return {
    placement: {
      kind: "object_anchored",
      anchor: "simulated_engine_bay",
      position: pos,
      sizeMeters: [0.6, 0.3, 0.4],
    },
    anchorStrategy: "simulated_anchor",
    scaleMode: "real_scale",
    whyPlacement:
      "The serviced part is anchored to the real (or simulated) engine " +
      "component — its position MUST be the real position so the highlight " +
      "is unambiguous.",
  };
}

function explodedAboveTarget(element, layout, ctx) {
  // CRITICAL spatial rule from the product spec:
  //   "The exploded air filter assembly must align directly above the
  //    actual target housing." — do not tilt or randomly offset.
  const targetId = element._relatesToTargetElementId;
  const target = targetId ? ctx.elementsById.get(targetId) : null;
  if (target?.placement?.position) {
    const [tx, ty, tz] = target.placement.position;
    return {
      placement: {
        kind: "above_target",
        anchor: `above_target:${target.id}`,
        position: [tx, ty + 0.55, tz],
        // Stack components vertically along Y above the target.
        layout: "vertical_stack_along_Y",
        sizeMeters: [0.5, 0.7, 0.4],
      },
      anchorStrategy: "relative_to_target",
      scaleMode: "enlarged_detail",
      whyPlacement:
        "Aligned directly above the highlighted target housing — the spatial " +
        "mapping between an exploded part and its real position has to be " +
        "obvious without guessing or rotation tricks.",
    };
  }
  return tabletop(element, layout);
}

function anchoredCallout(element, layout, ctx) {
  const targetId = element._relatesToTargetElementId;
  const target = targetId ? ctx.elementsById.get(targetId) : null;
  if (target?.placement?.position) {
    const [tx, ty, tz] = target.placement.position;
    const n = bump(layout.counters, `callout_${targetId}`);
    return {
      placement: {
        kind: "object_anchored",
        anchor: `on_target:${target.id}`,
        position: [tx + 0.35, ty + 0.18 + n * 0.18, tz + 0.20],
        sizeMeters: [0.35, 0.18],
      },
      anchorStrategy: "relative_to_target",
      scaleMode: "compact_panel",
      whyPlacement:
        "Physical interaction points (clips, screws, ports) only make sense " +
        "pinned directly on the target — the label sits *on* the part it " +
        "names.",
    };
  }
  return inFrontOfUser(element, layout);
}

function diagnosticOverlay(element, layout, ctx) {
  // Diagnoses float ABOVE the highlighted target, distinct from any
  // anchored markers, so the user reads them as commentary on the part.
  const targetId = element._relatesToTargetElementId;
  const target = targetId ? ctx.elementsById.get(targetId) : null;
  if (target?.placement?.position) {
    const [tx, ty, tz] = target.placement.position;
    return {
      placement: {
        kind: "above_target",
        anchor: `diag_above_target:${target.id}`,
        position: [tx - 0.5, ty + 0.65, tz - 0.05],
        sizeMeters: [0.7, 0.22],
      },
      anchorStrategy: "relative_to_target",
      scaleMode: "compact_panel",
      whyPlacement:
        "A diagnosis explains *why* the user is here — floated above the " +
        "target so it reads as commentary on the physical part rather than " +
        "as another sticker on it.",
    };
  }
  return inFrontOfUser(element, layout);
}

function guideLine(element, layout, ctx) {
  // A line needs both endpoints; we record them in the placement so the
  // viewer / Vision Pro player can draw the segment without re-resolving.
  const fromId = element.sourceContent?.fromElementId;
  const toId = element.sourceContent?.toElementId;
  const from = fromId ? ctx.elementsById.get(fromId) : null;
  const to = toId ? ctx.elementsById.get(toId) : null;
  if (from?.placement?.position && to?.placement?.position) {
    const fp = from.placement.position;
    const tp = to.placement.position;
    return {
      placement: {
        kind: "above_target",
        anchor: `guide:${fromId}->${toId}`,
        position: [(fp[0] + tp[0]) / 2, (fp[1] + tp[1]) / 2, (fp[2] + tp[2]) / 2],
        from: fp,
        to: tp,
        sizeMeters: [0.02, Math.abs(fp[1] - tp[1]) || 0.5, 0.02],
      },
      anchorStrategy: "relative_to_target",
      scaleMode: "real_scale",
      whyPlacement:
        "A guide line is a visual derivative of two other elements — its " +
        "endpoints are recomputed from their placements so it stays correct " +
        "if either moves.",
    };
  }
  return inFrontOfUser(element, layout);
}

function inFrontOfUser(element, layout) {
  const slot = layout.in_front_of_user;
  const pos = [slot.nextX, 1.4, 1.0];
  slot.nextX += 0.6;
  return {
    placement: {
      kind: "in_front_of_user",
      anchor: "user_front",
      position: pos,
      sizeMeters: [0.5, 0.35],
    },
    anchorStrategy: "user_relative",
    scaleMode: "compact_panel",
    whyPlacement: "Fallback — placed in front of the user at reading distance.",
  };
}

function bump(counters, key) {
  const n = counters[key] || 0;
  counters[key] = n + 1;
  return n;
}

export const ROOM_DIMENSIONS = ROOM;
