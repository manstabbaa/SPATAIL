// ContractEnums.swift
//
// Closed enums that mirror the SPATAIL contract vocabularies. The
// contract ships these as strings; the iOS renderer switches on them.
// Keep these in lockstep with:
//   /pipeline/spatail/experience_contract.js
//   /schemas/spatialExperienceContract.schema.json

import Foundation

enum RepresentationMode: String, Codable, Hashable {
    case two_d_panel
    case wall_dashboard
    case three_d_model
    case tabletop_model
    case floor_timeline
    case floating_decision_card
    case highlighted_target
    case exploded_view
    case anchored_callout
    case guide_line
    case diagnostic_overlay

    var displayName: String {
        switch self {
        case .two_d_panel:             return "2D panel"
        case .wall_dashboard:          return "Wall dashboard"
        case .three_d_model:           return "3D model"
        case .tabletop_model:          return "Tabletop model"
        case .floor_timeline:          return "Floor timeline"
        case .floating_decision_card:  return "Floating decision card"
        case .highlighted_target:      return "Highlighted target"
        case .exploded_view:           return "Exploded view"
        case .anchored_callout:        return "Anchored callout"
        case .guide_line:              return "Guide line"
        case .diagnostic_overlay:      return "Diagnostic overlay"
        }
    }
}

enum ContentTypeTag: String, Codable, Hashable {
    case summary_panel
    case numeric_summary
    case list
    case step_sequence
    case timeline
    case decision_set
    case physical_target
    case assembly_explode
    case anchored_marker
    case diagnostic_finding
    case alignment_guide
    case process_model
    case environment
}

enum Placement: String, Codable, Hashable {
    case wall
    case table
    case floor
    case object_anchored
    case above_target
    case near_user
    case near_presenter
    case left_of_user
    case right_of_user
    case in_front_of_user
    case room_center

    var displayName: String {
        rawValue.replacingOccurrences(of: "_", with: " ")
    }
}

enum AnchorStrategy: String, Codable, Hashable {
    case world_anchor
    case plane_anchor
    case object_anchor
    case relative_to_target
    case user_relative
    case simulated_anchor
}

enum ScaleMode: String, Codable, Hashable {
    case real_scale
    case tabletop_scale
    case enlarged_detail
    case compact_panel
    case room_scale
}

enum AttentionBehavior: String, Codable, Hashable {
    case ambient
    case persistent_context
    case active_focus
    case peripheral
    case on_demand
    case guiding
}

enum InteractionMode: String, Codable, Hashable {
    case reset_view
    case highlight
    case isolate
    case explode
    case collapse
    case focus
    case next_step
    case previous_step
    case select
    case expand
}
