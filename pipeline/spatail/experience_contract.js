// SpatialExperienceContract — the v0.1 SPATAIL contract.
//
// Superset of the legacy SpatialSceneContract: a scene can now contain
// many spatial elements of different representation modes (panels, 3D
// models, exploded views, floor timelines, etc.), each with its own
// placement and its own reasoning.
//
// Same consumption model as before: the web viewer reads this JSON today,
// the visionOS player reads the same JSON later. No code generation.

export const SPATAIL_SCHEMA_VERSION = "0.5.0-spatail";

// v0.5 — explanation mechanics. The orchestrator picks one or more
// mechanic kinds per prompt, each mechanic produces or anchors a set of
// spatialElements, and the existing animation / interaction layers ride
// on top unchanged.
//
// Closed vocab. Each name in this list MUST have:
//   - spec/mechanics/v1/<name>.json   (param + source-shape manifest)
//   - viewer/mechanics/handlers/<name>.js  (renderer; can be a stub for
//     the placeholder mechanics, but the file must exist)
//
// "Shipped" mechanics carry a `qualityBar` reference example in their
// spec; "placeholder" mechanics route to the closest shipped neighbour
// when picked by the orchestrator, with `placeholderFor` set on the
// emitted mechanic so the viewer can warn.
export const MECHANIC_KINDS = [
  // Already shipped (renderers existed before v0.5)
  "exploded_view",
  "annotated_callouts",
  "highlighted_region",
  "timeline",
  // v0.5 — top-5 priority
  "cross_section",
  "assembly_sequence",
  "ghosted_internal",
  "flow_diagram",
  "process_animation",
  // Rest of the library — speced now, renderers land later
  "cutaway",
  "disassembly_sequence",
  "before_after",
  "comparison_grid",
  "metric_dashboard",
  "cross_reference",
  "scale_reference",
  "color_coded_map",
  "force_arrows",
  "particle_flow",
  "cutaway_orbit",
  "xray_layer_stack",
  "interactive_dissection",
  "placeholder_mechanic",  // when nothing fits; viewer renders a stub panel
];

// Closed vocab for the presentation layout. The orchestrator's compose
// stage assigns one. `stage_in_front` is the default while we are
// deliberately not placing into a real room.
export const PRESENTATION_LAYOUTS = [
  "stage_in_front",   // everything anchored ~1.5m ahead of the user
  "flat_grid",        // 2D grid of panels, no depth — useful when content is mostly textual
  "scene_floor",      // anchored to the room's floor plane (room-aware path; unused today)
  "wall_room",        // anchored to walls (room-aware; unused today)
];

// Fidelity of a spatial element — drives the AR renderer's confidence
// treatment. New prompts land at `ghost`; the user touching / accepting
// an element promotes it. `authored` is a Blender pass that the human
// curated and we should not auto-mutate.
export const FIDELITIES = [
  "ghost",      // dotted wireframe, low opacity, will replan freely
  "draft",      // styled but tentative, accepts edits
  "committed",  // user has confirmed; planner won't auto-move it
  "authored",   // came from a curated authoring pass (Blender etc.)
];

// Closed vocab for animation primitives. Adding one = new spec file in
// /schema/animations/v1/, one viewer handler under /viewer/animations/handlers/,
// and (when relevant) one Blender exporter branch. Keep the names short
// and verb-shaped — the viewer switches on these strings.
export const ANIMATION_PRIMITIVES = [
  "transform_keyframes",   // baked translate/rotate/scale tracks from glTF or sidecar
  "explode",               // radial outward from parent centroid along local +Y
  "assemble",              // reverse of explode, eased
  "highlight_pulse",       // emissive throb on a material slot
  "fade",                  // opacity 0 <-> 1
  "set_visible",           // discrete on/off at a beat
  "attention_camera_hint", // hint for the viewer / AR client to dolly attention
];

// Closed vocab for interaction triggers. A trigger fires actions[].
export const INTERACTION_TRIGGERS = [
  "tap",         // discrete pointer / hand tap on a target
  "hover",       // pointer / gaze hover on a target
  "dwell",       // sustained gaze for N ms on a target
  "scene_event", // emitted by SequenceController (e.g. "sequence_started")
];

// Closed vocab for interaction actions.
export const INTERACTION_ACTIONS = [
  "play_animation",   // ref = animation id
  "stop_animation",   // ref = animation id
  "advance_step",     // optional ref = step id; default is current+1
  "previous_step",
  "restart_sequence", // ref = sequence id
  "set_visible",      // ref = element id, params.visible bool
];

// Vocabularies. Closed enums so the viewer + Vision Pro player can switch
// on them safely. New values are added in lockstep with renderers.

export const CONTENT_TYPES = [
  "summary_panel",         // short readable text (status, insurance, notes)
  "numeric_summary",       // KPIs / numbers
  "list",                  // bullet list (materials, tools)
  "step_sequence",         // ordered procedure
  "timeline",              // events over time
  "decision_set",          // a small set of selectable next actions
  "physical_target",       // a real or simulated object the user works on
  "assembly_explode",      // parts of a physical_target shown apart
  "diagnostic_finding",    // a real diagnosis explaining *why* a task exists
  "anchored_marker",       // a labelled feature pinned to a physical target
  "alignment_guide",       // a visual line connecting two elements
  "airflow_streamlines",   // animated streamlines around a hero, optionally per regime
  "process_model",         // 3D model of a system / factory / mechanism
  "environment",           // larger 3D environment / room
];

export const REPRESENTATION_MODES = [
  "two_d_panel",            // flat readable text panel
  "wall_dashboard",         // larger multi-cell panel on a wall
  "three_d_model",          // generic 3D object placed in the scene
  "tabletop_model",         // 3D model placed on a table surface
  "floor_timeline",         // walkable sequence on the floor
  "floating_decision_card", // selectable card in the user's hand-reach zone
  "highlighted_target",     // physical object emphasized in place
  "exploded_view",          // multi-part 3D assembly shown apart
  "anchored_callout",       // small label/marker pinned to an object
  "guide_line",             // visual line connecting elements
  "diagnostic_overlay",     // floating diagnosis explaining a finding
  "airflow_field",          // animated streamline flow around a target (e.g. wind tunnel)
];

export const PLACEMENTS = [
  "wall",
  "table",
  "floor",
  "object_anchored",
  "above_target",
  "near_user",
  "near_presenter",
  "left_of_user",
  "right_of_user",
  "in_front_of_user",
  "room_center",
];

export const ANCHOR_STRATEGIES = [
  "world_anchor",       // anchored to a fixed point in world space
  "plane_anchor",       // anchored to a detected horizontal/vertical plane
  "object_anchor",      // anchored to a recognized real or simulated object
  "relative_to_target", // position is derived from another element
  "user_relative",      // travels with the user's head / body
  "simulated_anchor",   // viewer / AR runtime fakes the anchor (no detection yet)
];

export const SCALE_MODES = [
  "real_scale",         // 1:1 with the real world
  "tabletop_scale",     // shrunk to fit a tabletop
  "enlarged_detail",    // grown for legibility (exploded views, dashboards)
  "compact_panel",      // small persistent reference panel
  "room_scale",         // fills the room (floor timelines, walls)
];

export const ATTENTION_BEHAVIORS = [
  "ambient",            // always present, low salience
  "persistent_context", // always visible reference (status, insurance)
  "active_focus",       // currently in focus per the attention plan
  "peripheral",         // intentionally off to the side
  "on_demand",          // hidden until the user activates it
  "guiding",            // points the user toward something else
];

// FIDELITIES + DEFAULT_FIDELITY live at the top of this file alongside
// the other closed vocabularies. The renderer styles per fidelity:
//   ghost     — dotted wireframe + intent label, low opacity, will re-plan freely
//   draft     — styled but tentative, solid colour block, accepts edits
//   committed — user-confirmed; planner won't auto-move it; real geometry + material
//   authored  — Blender pass + animations + polish (senior-artist seal)
export const DEFAULT_FIDELITY = "committed";

// --------------------------------------------------------------------------
// Builder
// --------------------------------------------------------------------------
//
// All the layers (understanding / representation / placement) produce
// well-formed pieces, and this builder is the place that assembles the
// final object in the documented field order. Doing the assembly here
// means the schema lives in exactly one file.

export function buildContract({
  experienceId,
  title,
  sourcePrompt,
  sourceInputs,
  sourceFiles,
  detectedDomain,
  environmentAssumptions,
  spatialElements,
  relationships,
  interactionPlan,
  attentionPlan,
  assetRequirements,
  reasoningSummary,
  // v0.3 — modular animation / interaction / sequence layer. All optional;
  // an experience with no animations[] simply renders static. The viewer
  // never assumes any of these exist.
  animations,
  interactions,
  sequences,
  defaultSequenceId,
  // v0.4 — optional room input. When the iOS app passes its captured
  // RoomContract through to the planner, this block ships back inside
  // the resulting contract for the viewer to render against real surfaces.
  roomContract,
  // v0.5 — explanation orchestrator output. Both optional; a contract
  // produced by older code paths skips these and renders as before.
  explanation,    // { written, intentSummary }
  mechanics,      // [{ id, kind, target, params, why, anchorsOn? }]
  presentation,   // { layout, ordering }
}) {
  return {
    schemaVersion: SPATAIL_SCHEMA_VERSION,
    createdAt: new Date().toISOString(),

    experienceId,
    title,
    sourcePrompt,
    sourceInputs: sourceInputs || [],
    sourceFiles: sourceFiles || [],
    detectedDomain,
    environmentAssumptions: environmentAssumptions || {},

    spatialElements: spatialElements || [],
    relationships: relationships || [],
    interactionPlan: interactionPlan || { interactions: [] },
    attentionPlan: attentionPlan || [],
    assetRequirements: assetRequirements || [],

    // v0.3 layer — declarative animation, trigger-action interactions,
    // and timed sequences that compose them into a story.
    animations: animations || [],
    interactions: interactions || [],
    sequences: sequences || [],
    defaultSequenceId: defaultSequenceId || null,

    // v0.4 — the room the planner placed against (when provided).
    roomContract: roomContract || null,

    // v0.5 — explanation orchestrator output. The viewer treats these
    // as inspectable artefacts: written explanation in a sidebar, the
    // mechanic list as chips, the presentation layout as the renderer
    // contract for where elements live on screen.
    explanation: explanation || null,
    mechanics: mechanics || [],
    presentation: presentation || null,

    reasoningSummary: reasoningSummary || "",

    // Closed enums published in-band so any consumer (viewer, Vision Pro
    // runtime, agent) can validate without importing this module.
    vocabularies: {
      contentTypes: CONTENT_TYPES,
      representationModes: REPRESENTATION_MODES,
      placements: PLACEMENTS,
      anchorStrategies: ANCHOR_STRATEGIES,
      scaleModes: SCALE_MODES,
      attentionBehaviors: ATTENTION_BEHAVIORS,
      animationPrimitives: ANIMATION_PRIMITIVES,
      interactionTriggers: INTERACTION_TRIGGERS,
      interactionActions: INTERACTION_ACTIONS,
      fidelities: FIDELITIES,
      mechanicKinds: MECHANIC_KINDS,
      presentationLayouts: PRESENTATION_LAYOUTS,
    },
  };
}

// Per-element factory. Keeps required fields explicit and ordered, and
// makes it obvious which fields are *every element's responsibility* to
// fill in (reasoning fields are not optional — the product principle is
// that every spatial decision is explainable).
export function buildSpatialElement({
  id,
  title,
  contentType,
  representationMode,
  placement,
  anchorStrategy,
  scaleMode,
  priority,
  sourceContent,
  requiredAssets,
  fallbackGeometry,
  interactions,
  attentionBehavior,
  whyThisRepresentation,
  whyThisPlacement,
  // v0.3 — per-element animation hooks. Both optional.
  defaultAnimation,   // animation id played when the element first activates
  respondsTo,         // interaction ids this element responds to
  // v0.4 — confidence treatment + resolved real-room surface.
  fidelity,           // one of FIDELITIES, default 'draft'
  resolvedSurface,    // { surfaceId, kind, area, normal, centroid } when planned with a room
}) {
  if (!id) throw new Error("buildSpatialElement: id is required");
  if (!REPRESENTATION_MODES.includes(representationMode)) {
    throw new Error(
      `buildSpatialElement(${id}): representationMode "${representationMode}" ` +
      `is not in the closed vocabulary`,
    );
  }
  if (placement?.kind && !PLACEMENTS.includes(placement.kind)) {
    throw new Error(
      `buildSpatialElement(${id}): placement.kind "${placement.kind}" ` +
      `is not in the closed vocabulary`,
    );
  }
  if (anchorStrategy && !ANCHOR_STRATEGIES.includes(anchorStrategy)) {
    throw new Error(
      `buildSpatialElement(${id}): anchorStrategy "${anchorStrategy}" ` +
      `is not in the closed vocabulary`,
    );
  }
  if (scaleMode && !SCALE_MODES.includes(scaleMode)) {
    throw new Error(
      `buildSpatialElement(${id}): scaleMode "${scaleMode}" ` +
      `is not in the closed vocabulary`,
    );
  }
  if (attentionBehavior && !ATTENTION_BEHAVIORS.includes(attentionBehavior)) {
    throw new Error(
      `buildSpatialElement(${id}): attentionBehavior "${attentionBehavior}" ` +
      `is not in the closed vocabulary`,
    );
  }
  if (fidelity && !FIDELITIES.includes(fidelity)) {
    throw new Error(
      `buildSpatialElement(${id}): fidelity "${fidelity}" ` +
      `is not in the closed vocabulary (${FIDELITIES.join(", ")})`,
    );
  }
  if (!whyThisRepresentation) {
    throw new Error(
      `buildSpatialElement(${id}): whyThisRepresentation is required — ` +
      "every spatial decision must be explainable",
    );
  }
  if (!whyThisPlacement) {
    throw new Error(
      `buildSpatialElement(${id}): whyThisPlacement is required — ` +
      "every spatial decision must be explainable",
    );
  }
  return {
    id,
    title: title || id,
    contentType,
    representationMode,
    placement: placement || {},
    anchorStrategy,
    scaleMode,
    priority: priority ?? 50,
    sourceContent: sourceContent || null,
    requiredAssets: requiredAssets || [],
    fallbackGeometry: fallbackGeometry || "panel",
    interactions: interactions || [],
    attentionBehavior: attentionBehavior || "ambient",
    defaultAnimation: defaultAnimation || null,
    respondsTo: respondsTo || [],
    // v0.4 — fidelity gradient. Without a hint from the planner we mark
    // the element `committed`: it has real geometry + a real placement,
    // because the only way to get here is to have passed the closed-vocab
    // validation above. `ghost` and `draft` are emitted by the prompt
    // re-planner before commit; `authored` is bumped when an authored
    // animation bundle is present for the element.
    fidelity: fidelity || DEFAULT_FIDELITY,
    resolvedSurface: resolvedSurface || null,
    whyThisRepresentation,
    whyThisPlacement,
  };
}
