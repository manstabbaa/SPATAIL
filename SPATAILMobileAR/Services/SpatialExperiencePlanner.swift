// SpatialExperiencePlanner.swift
//
// v1: thin pass-through to the bundled contracts produced by the
// Node SPATAIL backend (`npm run spatail`). The interface is the same
// one a future on-device planner will implement — given a card,
// return a spatial experience plan/contract.
//
// Keeping this protocol-shaped now means the GeneratedExperienceView
// doesn't need to change when the planner moves on-device.

import Foundation

protocol SpatialExperiencePlanning {
    func plan(forIntent intent: PromptIntent) throws -> SpatialExperienceContract
    func plan(forContractId id: String) throws -> SpatialExperienceContract
}

final class SpatialExperiencePlanner: SpatialExperiencePlanning {
    private let ingestion: ContentIngesting

    init(ingestion: ContentIngesting) {
        self.ingestion = ingestion
    }

    func plan(forIntent intent: PromptIntent) throws -> SpatialExperienceContract {
        switch intent {
        case .mustangService:
            return try ingestion.loadContract(id: "mustang-service")
        case .q3Review:
            return try ingestion.loadContract(id: "q3-manufacturing-review")
        case .unknown(let raw):
            throw PlannerError.unsupportedIntent(prompt: raw)
        }
    }

    func plan(forContractId id: String) throws -> SpatialExperienceContract {
        try ingestion.loadContract(id: id)
    }

    enum PlannerError: LocalizedError {
        case unsupportedIntent(prompt: String)
        var errorDescription: String? {
            switch self {
            case .unsupportedIntent(let p):
                return "v1 only knows the two bundled demos; got prompt: \(p)"
            }
        }
    }
}
