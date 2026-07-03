// PlacementReportWire.swift — the client-attribution body POSTed to the job
// server's /placement-report (LIVE_BRAIN_SPEC §2.2, field-for-field).
//
// Placement honesty: after the runtime applies a contract, it reports what it
// actually DID — the solver inputs it saw, the plan it computed, and the final
// world transforms it rendered — so /traces/view can show intent vs reality
// side by side and highlight where they disagree.

import Foundation
import simd

struct PlacementReportWire: Codable {
    var experienceId: String
    var client: String = "ios"                    // "ios" | "web"
    var reportedAt: Double                        // epoch seconds
    var solverInputs: SolverInputs
    var plan: Plan
    var finalPlacements: [FinalPlacement]

    struct SolverInputs: Codable {
        var anchorPreference: String              // e.g. "table"
        var scaleMode: String
        var coverage: Double                      // max surface coverage budget
        var footprints: [Footprint]
        var roomSummary: RoomSummary
    }

    struct Footprint: Codable {
        var assetId: String
        var meters: [Double]                      // [x, y, z]
        /// Provenance (spec §2.3): "library" | "object_size_llm" | "default_guess"
        var source: String
    }

    struct RoomSummary: Codable {
        var surfaces: [SurfaceSummary]
    }

    struct SurfaceSummary: Codable {
        var kind: String                          // e.g. "table"
        var sizeMeters: [Double]                  // [w, d]
        var y: Double

        init(kind: String, sizeMeters: [Double], y: Double) {
            self.kind = kind
            self.sizeMeters = sizeMeters
            self.y = y
        }

        /// Summarize a scanned surface: footprint from the boundary's XZ extent.
        init(surface s: RoomSurface) {
            kind = s.kind.rawValue
            var lo = SIMD2<Float>(repeating: .greatestFiniteMagnitude)
            var hi = SIMD2<Float>(repeating: -.greatestFiniteMagnitude)
            for v in s.boundary {
                lo = simd_min(lo, SIMD2(v.x, v.z))
                hi = simd_max(hi, SIMD2(v.x, v.z))
            }
            if lo.x > hi.x { lo = .zero; hi = .zero }
            sizeMeters = [Double(hi.x - lo.x), Double(hi.y - lo.y)]
            y = Double(s.y)
        }
    }

    struct Plan: Codable {
        var anchor: String
        var placements: [PlannedPlacement]
    }

    struct PlannedPlacement: Codable {
        var elementId: String
        var position: [Double]                    // [x, y, z] world metres
        var yaw: Double                           // radians about +Y
        var scale: Double
        var fits: Bool
        var reason: String
    }

    struct FinalPlacement: Codable {
        var elementId: String
        var worldTransform: [Float]               // 16 floats, column-major
        var renderScale: Double
        /// Human-readable nudges applied after the solve, e.g. "table-pin -0.02m".
        var corrections: [String]
    }
}
