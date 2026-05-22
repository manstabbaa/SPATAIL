// ContentIngestionService.swift
//
// Loads SpatialExperienceContract.json files from the app bundle.
// In v1 the contracts are produced offline by the Node SPATAIL backend
// and copied into Resources/. The interface deliberately accepts a
// contract identifier (not a file path) so a future ingestion path —
// authoring a ContentCard on-device, posting it to the backend, fetching
// the resulting contract — can drop in behind the same call site.

import Foundation

protocol ContentIngesting {
    func availableContracts() -> [BundledContractRef]
    func loadContract(id: String) throws -> SpatialExperienceContract
}

struct BundledContractRef: Identifiable, Hashable {
    let id: String
    let title: String
    let domain: String
    let resourceName: String   // file in Resources/ without .json
}

final class ContentIngestionService: ContentIngesting {
    // Hand-curated for v1 to keep the splash screen deterministic. The
    // two contracts below match the bundled Resources/.
    private let demos: [BundledContractRef] = [
        BundledContractRef(
            id: "mustang-service",
            title: "Mustang Service Assistant",
            domain: "vehicle_maintenance",
            resourceName: "mustang-service-spatial-contract",
        ),
        BundledContractRef(
            id: "q3-manufacturing-review",
            title: "Q3 Manufacturing Cost Review",
            domain: "corporate_review",
            resourceName: "q3-manufacturing-review-spatial-contract",
        ),
    ]

    func availableContracts() -> [BundledContractRef] { demos }

    func loadContract(id: String) throws -> SpatialExperienceContract {
        guard let ref = demos.first(where: { $0.id == id }) else {
            throw IngestionError.unknownContract(id: id)
        }
        guard let url = Bundle.main.url(forResource: ref.resourceName, withExtension: "json") else {
            throw IngestionError.missingResource(name: ref.resourceName)
        }
        let data = try Data(contentsOf: url)
        do {
            return try JSONDecoder().decode(SpatialExperienceContract.self, from: data)
        } catch {
            throw IngestionError.decode(name: ref.resourceName, underlying: error)
        }
    }

    enum IngestionError: LocalizedError {
        case unknownContract(id: String)
        case missingResource(name: String)
        case decode(name: String, underlying: Error)

        var errorDescription: String? {
            switch self {
            case .unknownContract(let id):
                return "No bundled contract with id '\(id)'."
            case .missingResource(let name):
                return "Resource '\(name).json' is missing from the app bundle. " +
                       "Refresh with `npm run spatail` and copy to SPATAILMobileAR/Resources/."
            case .decode(let name, let underlying):
                return "Failed to decode '\(name).json': \(underlying)"
            }
        }
    }
}
