import Foundation
import simd

// Places experience stations in the user's space on a comfort arc, grounded in the
// captured Apple HIG (docs/apple-visionos/hig/spatial-layout + immersive-experiences):
//   - world-anchored, never head-locked
//   - within the ~1.5 m comfort bubble; "bring the object to the user, don't make them move"
//   - centered in the field of view; stations face the learner
//   - neighbours clear by the spec's spacing_min_m
// Platform-neutral math (SIMD only) so iOS and visionOS share it.

struct StationPose {
    let index: Int
    let position: SIMD3<Float>   // metres, relative to the experience anchor (y up)
    let yawRadians: Float        // rotation about y so the station faces the user
}

enum StationLayout {
    /// Compute poses for `count` stations given the placement rules and each
    /// station's footprint width (metres). The user is assumed at the origin
    /// looking along -Z (RealityKit convention); content sits in front (-Z).
    static func poses(count: Int,
                      footprintWidths: [Float],
                      comfortRadiusM: Double,
                      spacingMinM: Double,
                      layout: String) -> [StationPose] {
        guard count > 0 else { return [] }
        let radius = Float(min(max(comfortRadiusM, 0.4), 1.5))   // clamp to comfort bubble
        let gap = Float(max(spacingMinM, 0.05))
        let maxW = max(footprintWidths.max() ?? 0.3, 0.1)

        if count == 1 {
            // single station: straight ahead, on the comfort plane, facing the user
            return [StationPose(index: 0,
                                position: SIMD3(0, 0, -radius),
                                yawRadians: 0)]
        }

        switch layout {
        case "line":
            // a straight row in front of the user, centered, clearing widths+gap
            let step = maxW + gap
            let span = step * Float(count - 1)
            return (0..<count).map { i in
                let x = -span / 2 + step * Float(i)
                return StationPose(index: i,
                                   position: SIMD3(x, 0, -radius),
                                   yawRadians: 0)
            }
        case "cluster":
            // loose 2-row cluster (used for >3 small stations); still within radius
            return (0..<count).map { i in
                let row = i / 3, col = i % 3
                let step = maxW + gap
                let x = (Float(col) - 1) * step
                let z = -radius + Float(row) * (maxW + gap) * 0.6
                return StationPose(index: i, position: SIMD3(x, 0, z), yawRadians: 0)
            }
        default: // "arc" — fan curved toward the user, equidistant, each facing them
            // angular step so neighbour footprints clear by gap at this radius
            let step = 2 * asin(min(0.99, (maxW + gap) / (2 * radius)))
            let maxSpread: Float = 64 * .pi / 180     // comfort cap (HIG: avoid head-turning)
            let spread = min(step * Float(count - 1), maxSpread)
            let realStep = count > 1 ? spread / Float(count - 1) : 0
            return (0..<count).map { i in
                let ang = -spread / 2 + realStep * Float(i)   // 0 = straight ahead
                let x = radius * sin(ang)
                let z = -radius * cos(ang)
                // face the user (who is at origin): yaw toward origin
                let yaw = atan2(x - 0, z - 0)   // direction from station to user, about y
                return StationPose(index: i, position: SIMD3(x, 0, z), yawRadians: yaw)
            }
        }
    }
}
