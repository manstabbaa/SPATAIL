// SimulatedDiagnosticsService.swift
//
// v1 stub: returns the canned "dirty filter" finding the Mustang demo
// shows. The interface deliberately mirrors what a future real
// diagnostics call would return (severity + anchor reference) so the
// callout renderer doesn't change.

import Foundation

protocol SimulatedDiagnosing {
    func mustangDirtyFilterFinding() -> DiagnosticFinding
}

final class SimulatedDiagnosticsService: SimulatedDiagnosing {
    func mustangDirtyFilterFinding() -> DiagnosticFinding {
        DiagnosticFinding(
            title: "Dirty filter",
            detail: "Air filter appears dirty — light no longer passes " +
                    "through the pleats. Recommend replacement now.",
            severity: "fix_now",
            anchorObjectId: "air_filter_housing",
        )
    }
}
