// RepresentationSelector.swift
//
// v1 stub. The bundled contracts already carry per-element
// representation choices. This protocol exists for the next iteration,
// when the iPhone constructs cards locally and needs to pick a mode
// without contacting the backend.
//
// The implementation mirrors the logic in
// /pipeline/spatail/representation_selector.js — keep them in sync.

import Foundation

struct RepresentationChoice: Hashable {
    let mode: RepresentationMode
    let reason: String
}

protocol RepresentationSelecting {
    func select(forContentType type: ContentTypeTag,
                hint: RepresentationHint) -> RepresentationChoice
}

struct RepresentationHint: Hashable {
    /// True when the content is associated with a physical target via a
    /// targetRef — steps anchored to a part stay as a panel beside it.
    var hasTargetAnchor: Bool = false
    /// Number of facts in a bucket — >= 5 escalates a panel to a wall
    /// dashboard.
    var factCount: Int = 0
}

final class RepresentationSelector: RepresentationSelecting {
    func select(forContentType type: ContentTypeTag,
                hint: RepresentationHint) -> RepresentationChoice {
        switch type {
        case .summary_panel:
            if hint.factCount >= 5 {
                return .init(
                    mode: .wall_dashboard,
                    reason: "Multiple related facts read better as one wall " +
                            "dashboard than a tower of small panels.",
                )
            }
            return .init(
                mode: .two_d_panel,
                reason: "Short readable text — 2D panel keeps it scannable " +
                        "without occupying physical workspace.",
            )
        case .numeric_summary:
            return .init(
                mode: .wall_dashboard,
                reason: "KPIs are most useful at-a-glance; a wall dashboard " +
                        "groups them at glanceable scale.",
            )
        case .list:
            return .init(
                mode: .two_d_panel,
                reason: "A list is read top-to-bottom — a vertical 2D panel " +
                        "beats any 3D arrangement.",
            )
        case .step_sequence:
            if hint.hasTargetAnchor {
                return .init(
                    mode: .two_d_panel,
                    reason: "Steps must stay persistent while the user works — " +
                            "render as a readable panel, not buried in 3D.",
                )
            }
            return .init(
                mode: .floor_timeline,
                reason: "A standalone procedure with no anchored object " +
                        "becomes a walkable floor sequence.",
            )
        case .timeline:
            return .init(
                mode: .floor_timeline,
                reason: "Events over time map naturally to a path on the " +
                        "floor the user can walk along.",
            )
        case .decision_set:
            return .init(
                mode: .floating_decision_card,
                reason: "A small set of next-actions belongs in the user's " +
                        "hand-reach zone — floating selectable cards.",
            )
        case .physical_target:
            return .init(
                mode: .highlighted_target,
                reason: "The user is acting on a real object — emphasize it " +
                        "in place with a bright shader so the part is unmistakable.",
            )
        case .assembly_explode:
            return .init(
                mode: .exploded_view,
                reason: "An assembly's parts and order are best shown spread " +
                        "apart, aligned directly above the real target.",
            )
        case .anchored_marker:
            return .init(
                mode: .anchored_callout,
                reason: "A physical interaction point (clip, screw, port) " +
                        "only makes sense pinned directly to the part it sits on.",
            )
        case .diagnostic_finding:
            return .init(
                mode: .diagnostic_overlay,
                reason: "A diagnosis explains *why* the user is here — float " +
                        "it above the target as commentary, not another sticker.",
            )
        case .alignment_guide:
            return .init(
                mode: .guide_line,
                reason: "A line is the cheapest way to make 'this part comes " +
                        "from this real spot' obvious — draw it.",
            )
        case .process_model:
            return .init(
                mode: .tabletop_model,
                reason: "An inspectable process / system is best as a 3D " +
                        "model on a table — the user can walk around it.",
            )
        case .environment:
            return .init(
                mode: .tabletop_model,
                reason: "Environment models default to tabletop scale.",
            )
        }
    }
}
