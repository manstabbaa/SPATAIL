// RepairWorkflowService.swift
//
// v1 stub. Returns the same step list the demo card encodes. v2 will
// stream steps from a real workflow engine and advance the attention
// plan as the user signals "next" via gaze or pinch.

import Foundation

protocol RepairWorkflowing {
    func mustangAirFilterSteps() -> [RepairStep]
    func mustangRequiredTools() -> [ToolItem]
}

final class RepairWorkflowService: RepairWorkflowing {
    func mustangAirFilterSteps() -> [RepairStep] {
        [
            .init(index: 1, instruction: "Open the hood and prop it."),
            .init(index: 2, instruction: "Locate the air filter housing on the passenger side."),
            .init(index: 3, instruction: "Release the four spring clips around the airbox lid."),
            .init(index: 4, instruction: "Loosen the single T20 Torx retaining screw."),
            .init(index: 5, instruction: "Lift the airbox lid and remove the dirty filter."),
            .init(index: 6, instruction: "Seat the new filter with the rubber gasket facing down."),
            .init(index: 7, instruction: "Replace the lid, retighten the screw, re-seat the clips."),
        ]
    }

    func mustangRequiredTools() -> [ToolItem] {
        [
            .init(name: "Replacement engine air filter", note: "Motorcraft FA-1883"),
            .init(name: "Phillips-head screwdriver", note: nil),
            .init(name: "T20 Torx driver", note: nil),
            .init(name: "Clean shop rag", note: nil),
        ]
    }
}
