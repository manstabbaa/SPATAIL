// WallPanelBuilder.swift
//
// Larger, denser panel variant for wall_dashboard elements. Mostly a
// thin wrapper around SpatialPanelBuilder — the size hint already comes
// from the contract's `placement.sizeMeters`. Separated so a future
// implementation can swap in wall-anchor logic (e.g. snap to a
// detected ARPlaneAnchor with vertical alignment) without touching the
// generic panel path.

import Foundation
import RealityKit

struct WallPanelBuilder {
    func build(for element: SpatialElement) -> Entity {
        SpatialPanelBuilder().build(for: element, style: .wallDashboard)
    }
}
