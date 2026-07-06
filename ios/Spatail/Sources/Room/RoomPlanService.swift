// RoomPlanService.swift — Apple's furniture model as an entity source (v3 §2).
//
// RoomPlan's CapturedRoom.objects is a trained model asserting "there is a SOFA
// here, this big" — sofa/table/bed/chair/storage/tv as ONE oriented box each.
// That is exactly the couch problem solved natively, so it feeds the registry as
// high-prior furniture entities (provenance `.roomplan`; identity semantic via
// the local-label path — the VLM still outranks; geometry never displaces fresh
// MEASURED form).
//
// Session sharing: iOS 17's custom-ARSession initializer piggybacks the hub's
// session — one camera, one world. Two honesty rules:
//   • gated on RoomCaptureSession.isSupported (LiDAR) + the Settings toggle;
//   • the DEGRADATION WATCH (AppModel): if smoothed scene depth stops arriving
//     after RoomPlan starts, RoomPlan is stopped and the truth line says so —
//     the Form Engine's measurements outrank Apple's convenience.
//
// Delegate callbacks may arrive off-main; the mapping is done inline (cheap,
// value types) and results hop to main.

import Foundation
import ARKit
import simd
#if canImport(RoomPlan)
import RoomPlan
#endif

@MainActor
final class RoomPlanService: NSObject, ObservableObject {

    /// Throttle: CapturedRoom updates stream fast; the registry needs ~1 Hz.
    private static let ingestInterval: TimeInterval = 1.0

    @Published private(set) var running = false
    @Published private(set) var lastFurnitureCount = 0
    /// Why the service is off ("unsupported", "stopped: depth degraded", …) —
    /// surfaced by the Truth Overlay/Settings. nil while running or never started.
    @Published private(set) var stoppedReason: String?

    /// Registry hand-off, set by AppModel.
    var onFurniture: (([ObjectRegistry.NativeFurniture]) -> Void)?

    static var isSupported: Bool {
        #if canImport(RoomPlan)
        return RoomCaptureSession.isSupported
        #else
        return false
        #endif
    }

    #if canImport(RoomPlan)
    private var captureSession: RoomCaptureSession?
    private let lastIngestBox = NetBox<TimeInterval>(0)

    func start(arSession: ARSession) {
        guard Self.isSupported else { stoppedReason = "unsupported (no LiDAR)"; return }
        guard captureSession == nil else { return }
        let session = RoomCaptureSession(arSession: arSession)
        session.delegate = self
        var configuration = RoomCaptureSession.Configuration()
        configuration.isCoachingEnabled = false
        session.run(configuration: configuration)
        captureSession = session
        running = true
        stoppedReason = nil
        print("[RoomPlan] started on the shared AR session")
    }

    func stop(reason: String? = nil) {
        captureSession?.stop(pauseARSession: false)   // the Lens keeps the camera
        captureSession = nil
        running = false
        stoppedReason = reason
        if let reason { print("[RoomPlan] stopped: \(reason)") }
    }

    // MARK: CapturedRoom → registry furniture

    /// RoomPlan category → the class token FormPriors/the merge gates speak.
    private nonisolated static func label(for category: CapturedRoom.Object.Category)
        -> String? {
        switch category {
        case .sofa:          return "couch"
        case .chair:         return "chair"
        case .table:         return "table"
        case .bed:           return "bed"
        case .storage:       return "cabinet"
        case .refrigerator:  return "refrigerator"
        case .television:    return "tv"
        case .stove:         return "stove"
        case .oven:          return "oven"
        case .dishwasher:    return "dishwasher"
        case .sink:          return "sink"
        case .washerDryer:   return "washer"
        case .toilet:        return "toilet"
        case .bathtub:       return "bathtub"
        case .fireplace:     return "fireplace"
        case .stairs:        return "stairs"
        @unknown default:    return nil
        }
    }

    private nonisolated static func confidence(_ c: CapturedRoom.Confidence) -> Float {
        switch c {
        case .high:   return 0.9
        case .medium: return 0.7
        case .low:    return 0.5
        @unknown default: return 0.5
        }
    }

    /// CapturedRoom.Object → wire-canon OrientedBox: dimensions are full extents,
    /// the transform sits at the box center, yaw extracted from the X column
    /// (R_y: col0 = (c, 0, −s) in our convention).
    private nonisolated static func furniture(from objects: [CapturedRoom.Object])
        -> [ObjectRegistry.NativeFurniture] {
        objects.compactMap { obj in
            guard let label = label(for: obj.category) else { return nil }
            let t = obj.transform
            let center = SIMD3<Float>(t.columns.3.x, t.columns.3.y, t.columns.3.z)
            let yaw = atan2(-t.columns.0.z, t.columns.0.x)
            let dims = obj.dimensions
            guard dims.x.isFinite, dims.y.isFinite, dims.z.isFinite,
                  dims.x > 0.05, dims.y > 0.02, dims.z > 0.05 else { return nil }
            let obb = OrientedBox(center: center,
                                  extents: SIMD3(dims.x, dims.y, dims.z),
                                  yaw: yaw)
            return ObjectRegistry.NativeFurniture(label: label, obb: obb,
                                                  confidence: confidence(obj.confidence))
        }
    }
    #else
    func start(arSession: ARSession) { stoppedReason = "RoomPlan unavailable" }
    func stop(reason: String? = nil) {}
    #endif
}

#if canImport(RoomPlan)
extension RoomPlanService: RoomCaptureSessionDelegate {

    nonisolated func captureSession(_ session: RoomCaptureSession,
                                    didUpdate room: CapturedRoom) {
        // Throttle off-main; map inline (values only); hop results to main.
        let now = ProcessInfo.processInfo.systemUptime
        let due = lastIngestBox.withLock { last -> Bool in
            guard now - last >= Self.ingestInterval else { return false }
            last = now
            return true
        }
        guard due else { return }
        let furniture = Self.furniture(from: room.objects)
        Task { @MainActor [weak self] in
            guard let self, self.running else { return }
            self.lastFurnitureCount = furniture.count
            if !furniture.isEmpty { self.onFurniture?(furniture) }
        }
    }

    nonisolated func captureSession(_ session: RoomCaptureSession,
                                    didEndWith data: CapturedRoomData,
                                    error: Error?) {
        if let error {
            Task { @MainActor [weak self] in
                self?.stop(reason: "ended: \(error.localizedDescription)")
            }
        }
    }
}
#endif
