// VehicleKnowledgeService.swift
//
// Returns the canned Mustang profile the demo uses. v1 just hands back
// the same data the demo card encodes; v2 is where this becomes a real
// service (VIN lookup, OEM service intervals, etc.).

import Foundation

protocol VehicleKnowing {
    func mustangDemoProfile() -> VehicleProfile
    func mustangDemoInsurance() -> InsuranceInfo
    func mustangDemoServiceHistory() -> [ServiceHistoryItem]
}

final class VehicleKnowledgeService: VehicleKnowing {
    func mustangDemoProfile() -> VehicleProfile {
        VehicleProfile(
            year: 2019, make: "Ford", model: "Mustang", trim: "GT",
            vin: "1FA6P8CF0K5152198",
            mileage: 45_672,
            lastServiced: "2025-11-12",
        )
    }

    func mustangDemoInsurance() -> InsuranceInfo {
        InsuranceInfo(
            carrier: "GEICO",
            policyNumber: "4421-8839-22",
            validThrough: "2026-08-14",
            deductibleCollision: "$500",
            deductibleComprehensive: "$250",
            roadsideAssistance: "Available 24/7 — 1-800-424-3426",
        )
    }

    func mustangDemoServiceHistory() -> [ServiceHistoryItem] {
        [
            .init(date: "2025-11-12", summary: "Oil change (5W-30 synthetic), tire rotation"),
            .init(date: "2025-08-03", summary: "Brake fluid flush"),
            .init(date: "2025-04-19", summary: "Cabin air filter replaced"),
            .init(date: "2025-02-07", summary: "Battery load test (pass, 12.6V)"),
        ]
    }
}
