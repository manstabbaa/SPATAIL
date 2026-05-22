// ⚠️  GENERATED — DO NOT EDIT BY HAND
//
//  Source:    pipeline/spatail/experience_contract.js
//  Generator: tools/sync/gen_swift_vocab.mjs
//  Regenerated: 2026-05-22T21:15:48.243Z
//
//  To update: edit the JS file, then run `npm run sync:swift-vocab`.

import Foundation

public enum SpatailContract {
    public static let schemaVersion = "0.5.0-spatail"
}

/// Mirrors `MECHANIC_KINDS` from experience_contract.js.
public enum MechanicKind: String, Codable, CaseIterable, Sendable {
    case explodedView = "exploded_view"
    case annotatedCallouts = "annotated_callouts"
    case highlightedRegion = "highlighted_region"
    case timeline = "timeline"
    case crossSection = "cross_section"
    case assemblySequence = "assembly_sequence"
    case ghostedInternal = "ghosted_internal"
    case flowDiagram = "flow_diagram"
    case processAnimation = "process_animation"
    case cutaway = "cutaway"
    case disassemblySequence = "disassembly_sequence"
    case beforeAfter = "before_after"
    case comparisonGrid = "comparison_grid"
    case metricDashboard = "metric_dashboard"
    case crossReference = "cross_reference"
    case scaleReference = "scale_reference"
    case colorCodedMap = "color_coded_map"
    case forceArrows = "force_arrows"
    case particleFlow = "particle_flow"
    case cutawayOrbit = "cutaway_orbit"
    case xrayLayerStack = "xray_layer_stack"
    case interactiveDissection = "interactive_dissection"
    case placeholderMechanic = "placeholder_mechanic"
}

/// Mirrors `ANIMATION_PRIMITIVES` from experience_contract.js.
public enum AnimationPrimitive: String, Codable, CaseIterable, Sendable {
    case transformKeyframes = "transform_keyframes"
    case explode = "explode"
    case assemble = "assemble"
    case highlightPulse = "highlight_pulse"
    case fade = "fade"
    case setVisible = "set_visible"
    case attentionCameraHint = "attention_camera_hint"
}

/// Mirrors `INTERACTION_TRIGGERS` from experience_contract.js.
public enum InteractionTrigger: String, Codable, CaseIterable, Sendable {
    case tap = "tap"
    case hover = "hover"
    case dwell = "dwell"
    case sceneEvent = "scene_event"
}

/// Mirrors `INTERACTION_ACTIONS` from experience_contract.js.
public enum InteractionAction: String, Codable, CaseIterable, Sendable {
    case playAnimation = "play_animation"
    case stopAnimation = "stop_animation"
    case advanceStep = "advance_step"
    case previousStep = "previous_step"
    case restartSequence = "restart_sequence"
    case setVisible = "set_visible"
}

/// Mirrors `CONTENT_TYPES` from experience_contract.js.
public enum ContentType: String, Codable, CaseIterable, Sendable {
    case summaryPanel = "summary_panel"
    case numericSummary = "numeric_summary"
    case list = "list"
    case stepSequence = "step_sequence"
    case timeline = "timeline"
    case decisionSet = "decision_set"
    case physicalTarget = "physical_target"
    case assemblyExplode = "assembly_explode"
    case diagnosticFinding = "diagnostic_finding"
    case anchoredMarker = "anchored_marker"
    case alignmentGuide = "alignment_guide"
    case airflowStreamlines = "airflow_streamlines"
    case processModel = "process_model"
    case environment = "environment"
}

/// Mirrors `REPRESENTATION_MODES` from experience_contract.js.
public enum RepresentationMode: String, Codable, CaseIterable, Sendable {
    case twoDPanel = "two_d_panel"
    case wallDashboard = "wall_dashboard"
    case threeDModel = "three_d_model"
    case tabletopModel = "tabletop_model"
    case floorTimeline = "floor_timeline"
    case floatingDecisionCard = "floating_decision_card"
    case highlightedTarget = "highlighted_target"
    case explodedView = "exploded_view"
    case anchoredCallout = "anchored_callout"
    case guideLine = "guide_line"
    case diagnosticOverlay = "diagnostic_overlay"
    case airflowField = "airflow_field"
}

/// Mirrors `PLACEMENTS` from experience_contract.js.
public enum Placement: String, Codable, CaseIterable, Sendable {
    case wall = "wall"
    case table = "table"
    case floor = "floor"
    case objectAnchored = "object_anchored"
    case aboveTarget = "above_target"
    case nearUser = "near_user"
    case nearPresenter = "near_presenter"
    case leftOfUser = "left_of_user"
    case rightOfUser = "right_of_user"
    case inFrontOfUser = "in_front_of_user"
    case roomCenter = "room_center"
}

/// Mirrors `ANCHOR_STRATEGIES` from experience_contract.js.
public enum AnchorStrategy: String, Codable, CaseIterable, Sendable {
    case worldAnchor = "world_anchor"
    case planeAnchor = "plane_anchor"
    case objectAnchor = "object_anchor"
    case relativeToTarget = "relative_to_target"
    case userRelative = "user_relative"
    case simulatedAnchor = "simulated_anchor"
}

/// Mirrors `SCALE_MODES` from experience_contract.js.
public enum ScaleMode: String, Codable, CaseIterable, Sendable {
    case realScale = "real_scale"
    case tabletopScale = "tabletop_scale"
    case enlargedDetail = "enlarged_detail"
    case compactPanel = "compact_panel"
    case roomScale = "room_scale"
}

/// Mirrors `ATTENTION_BEHAVIORS` from experience_contract.js.
public enum AttentionBehavior: String, Codable, CaseIterable, Sendable {
    case ambient = "ambient"
    case persistentContext = "persistent_context"
    case activeFocus = "active_focus"
    case peripheral = "peripheral"
    case onDemand = "on_demand"
    case guiding = "guiding"
}

/// Mirrors `FIDELITIES` from experience_contract.js.
public enum Fidelity: String, Codable, CaseIterable, Sendable {
    case ghost = "ghost"
    case draft = "draft"
    case committed = "committed"
    case authored = "authored"
}

/// Mirrors `PRESENTATION_LAYOUTS` from experience_contract.js.
public enum PresentationLayout: String, Codable, CaseIterable, Sendable {
    case stageInFront = "stage_in_front"
    case flatGrid = "flat_grid"
    case sceneFloor = "scene_floor"
    case wallRoom = "wall_room"
}
