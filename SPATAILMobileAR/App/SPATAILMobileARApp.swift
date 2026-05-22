// SPATAILMobileARApp.swift
//
// App entry. Constructs the service graph once and hands it to the
// SwiftUI hierarchy via an EnvironmentObject. Keeping the wiring
// explicit (no DI container) makes the dependency map readable —
// every service is constructed exactly once, here.

import SwiftUI

@main
struct SPATAILMobileARApp: App {
    @StateObject private var environment = AppEnvironment.makeDefault()

    var body: some Scene {
        WindowGroup {
            AppRoot()
                .environmentObject(environment)
                .preferredColorScheme(.dark)
        }
    }
}

/// The app's service graph. Held as an ObservableObject so views can
/// inject specific services via @EnvironmentObject without restructure.
final class AppEnvironment: ObservableObject {
    let ingestion: ContentIngesting
    let promptClassifier: PromptIntentClassifying
    let planner: SpatialExperiencePlanning
    let representationSelector: RepresentationSelecting
    let placementEngine: SpatialPlacing
    let explanation: ExperienceExplaining
    let vehicleKnowledge: VehicleKnowing
    let diagnostics: SimulatedDiagnosing
    let repairs: RepairWorkflowing

    init(
        ingestion: ContentIngesting,
        promptClassifier: PromptIntentClassifying,
        planner: SpatialExperiencePlanning,
        representationSelector: RepresentationSelecting,
        placementEngine: SpatialPlacing,
        explanation: ExperienceExplaining,
        vehicleKnowledge: VehicleKnowing,
        diagnostics: SimulatedDiagnosing,
        repairs: RepairWorkflowing,
    ) {
        self.ingestion = ingestion
        self.promptClassifier = promptClassifier
        self.planner = planner
        self.representationSelector = representationSelector
        self.placementEngine = placementEngine
        self.explanation = explanation
        self.vehicleKnowledge = vehicleKnowledge
        self.diagnostics = diagnostics
        self.repairs = repairs
    }

    static func makeDefault() -> AppEnvironment {
        let ingestion = ContentIngestionService()
        return AppEnvironment(
            ingestion: ingestion,
            promptClassifier: PromptIntentClassifier(),
            planner: SpatialExperiencePlanner(ingestion: ingestion),
            representationSelector: RepresentationSelector(),
            placementEngine: SpatialPlacementEngine(),
            explanation: ExperienceExplanationService(),
            vehicleKnowledge: VehicleKnowledgeService(),
            diagnostics: SimulatedDiagnosticsService(),
            repairs: RepairWorkflowService(),
        )
    }
}
