// ExperienceExplanationService.swift
//
// Converts a SpatialExperienceContract into the small projection the
// pre-AR explanation screen needs: an ordered list of per-element
// decisions with mode + placement + the contract's own reasoning.
// The view stays dumb; this service is the only place that knows how
// to derive these tuples.

import Foundation

protocol ExperienceExplaining {
    func decisions(for contract: SpatialExperienceContract) -> [SpatialReasoningDecision]
    func attentionLines(for contract: SpatialExperienceContract) -> [AttentionLine]
}

struct AttentionLine: Hashable, Identifiable {
    let step: Int
    let focusElementId: String
    let focusTitle: String
    let narration: String
    var id: Int { step }
}

final class ExperienceExplanationService: ExperienceExplaining {
    func decisions(for contract: SpatialExperienceContract) -> [SpatialReasoningDecision] {
        contract.spatialElements.map { el in
            SpatialReasoningDecision(
                elementId: el.id,
                elementTitle: el.title,
                representationMode: el.representationModeEnum,
                placementKind: el.placement.kind ?? "—",
                anchorStrategy: el.anchorStrategy ?? "—",
                scaleMode: el.scaleMode ?? "—",
                attentionBehavior: el.attentionBehavior ?? "—",
                whyRepresentation: el.whyThisRepresentation,
                whyPlacement: el.whyThisPlacement,
            )
        }
    }

    func attentionLines(for contract: SpatialExperienceContract) -> [AttentionLine] {
        let titleById = Dictionary(uniqueKeysWithValues:
            contract.spatialElements.map { ($0.id, $0.title) })
        return contract.attentionPlan.map { step in
            AttentionLine(
                step: step.step,
                focusElementId: step.focusElementId,
                focusTitle: titleById[step.focusElementId] ?? step.focusElementId,
                narration: step.narration,
            )
        }
    }
}
