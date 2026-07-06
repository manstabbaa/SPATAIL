// RegistryFusionMath.swift — the pure math of ObjectRegistry's fusion semantics
// (LIVE_BRAIN_SPEC §3): OBB smoothing, label debounce, part-region clamping and the
// cap/lid/top fallback slice. Foundation + simd ONLY — no ARKit — so the exact
// shipped logic runs in an off-device test harness.

import Foundation
import simd

enum RegistryFusion {

    // MARK: Label debounce (spec §3)

    /// Adopt a label immediately at/above this confidence.
    static let instantAdoptConfidence: Float = 0.8
    /// Otherwise adopt after this many consecutive agreeing ticks.
    static let debounceTicks = 2

    /// One identity tick against one object: same label re-confirms; high confidence
    /// adopts immediately; otherwise a candidate must win `debounceTicks` consecutive
    /// ticks — and a DIFFERENT label must win the same debounce to replace a held one.
    static func debounce(label candidate: String, confidence: Float,
                         at timestamp: TimeInterval,
                         into obj: inout SpatailObject) {
        func adopt() {
            obj.label = candidate
            obj.confidence = confidence
            obj.lastIdentifiedAt = timestamp
            obj.pendingLabel = nil
            obj.pendingCount = 0
        }

        if let current = obj.label, current.caseInsensitiveCompare(candidate) == .orderedSame {
            // Same label re-confirmed — refresh identity freshness + confidence.
            obj.confidence = confidence
            obj.lastIdentifiedAt = timestamp
            obj.pendingLabel = nil
            obj.pendingCount = 0
        } else if confidence >= instantAdoptConfidence {
            adopt()
        } else if obj.pendingLabel?.caseInsensitiveCompare(candidate) == .orderedSame {
            obj.pendingCount += 1
            if obj.pendingCount >= debounceTicks { adopt() }
        } else {
            obj.pendingLabel = candidate
            obj.pendingCount = 1
        }
    }

    /// One ON-DEVICE detector tick against one object. Local labels are the WEAKER
    /// identity source: they fill an EMPTY identity through the same debounce
    /// (instant at high confidence) and re-confirm an equal held label — but they
    /// never dethrone a different held label and never evict a different pending
    /// candidate (the VLM flips identity through `debounce`; a local tick must not
    /// reset its progress).
    static func attachLocalLabel(_ candidate: String, confidence: Float,
                                 at timestamp: TimeInterval,
                                 into obj: inout SpatailObject) {
        func adopt() {
            obj.label = candidate
            obj.confidence = confidence
            obj.lastIdentifiedAt = timestamp
            obj.pendingLabel = nil
            obj.pendingCount = 0
        }

        if let current = obj.label {
            if current.caseInsensitiveCompare(candidate) == .orderedSame {
                obj.confidence = max(obj.confidence, confidence)
                obj.lastIdentifiedAt = timestamp
            }
            return
        }
        if let pending = obj.pendingLabel {
            guard pending.caseInsensitiveCompare(candidate) == .orderedSame else { return }
            obj.pendingCount += 1
            if obj.pendingCount >= debounceTicks || confidence >= instantAdoptConfidence {
                adopt()
            }
        } else if confidence >= instantAdoptConfidence {
            adopt()
        } else {
            obj.pendingLabel = candidate
            obj.pendingCount = 1
        }
    }

    // MARK: OBB smoothing

    /// Light exponential smoothing of center/extents; yaw blended along the shortest
    /// arc modulo π (rectangles are π-symmetric).
    static func smooth(old: OrientedBox, new: OrientedBox, alpha: Float) -> OrientedBox {
        let center = old.center + (new.center - old.center) * alpha
        let extents = old.extents + (new.extents - old.extents) * alpha
        var delta = (new.yaw - old.yaw).truncatingRemainder(dividingBy: .pi)
        if delta > .pi / 2 { delta -= .pi }
        if delta < -.pi / 2 { delta += .pi }
        var yaw = old.yaw + delta * alpha
        while yaw > .pi / 2 { yaw -= .pi }
        while yaw <= -.pi / 2 { yaw += .pi }
        return OrientedBox(center: center, extents: extents, yaw: yaw)
    }

    // MARK: Part regions (spec §3, Parts)

    /// Re-express a raw part OBB in the parent's yaw frame and clamp it fully inside
    /// the parent (a part region can never poke out of its object). The result is
    /// oriented to the parent, per spec.
    static func clamp(region raw: OrientedBox, into parent: OrientedBox) -> OrientedBox {
        let c = cos(parent.yaw), s = sin(parent.yaw)
        // World → parent-local (inverse of R_y(parent.yaw)) for the center offset.
        let d = raw.center - parent.center
        var local = SIMD3<Float>(c * d.x - s * d.z, d.y, s * d.x + c * d.z)

        // Clamp extents to the parent's, then the center so the region fits.
        let extents = simd_min(raw.extents, parent.extents)
        let room = (parent.extents - extents) / 2
        local = simd_clamp(local, -room, room)

        // Parent-local → world (R_y(parent.yaw): world.x = c*lx + s*lz, world.z = -s*lx + c*lz).
        let world = SIMD3<Float>(parent.center.x + c * local.x + s * local.z,
                                 parent.center.y + local.y,
                                 parent.center.z - s * local.x + c * local.z)
        return OrientedBox(center: world, extents: extents, yaw: parent.yaw)
    }

    /// The top `fraction` slice of an OBB — the cap/lid/top fallback (spec §3).
    static func topSlice(of parent: OrientedBox, fraction: Float) -> OrientedBox {
        let sliceHeight = parent.extents.y * fraction
        let centerY = parent.center.y + (parent.extents.y - sliceHeight) / 2
        return OrientedBox(center: SIMD3(parent.center.x, centerY, parent.center.z),
                           extents: SIMD3(parent.extents.x, sliceHeight, parent.extents.z),
                           yaw: parent.yaw)
    }
}
