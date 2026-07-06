// KeyframeStore.swift — Perception v3 §6: the phone's short-term visual memory.
//
// A bounded ring of keyframes — (timestamp, pose, intrinsics, depth copy,
// upright JPEG) — so that WHEN a VLM answer lands (0.5–3 s late, ambient or
// focus), its boxes are resolved against the EXACT geometry of the frame that
// was seen, and hi-res crops of any object can be cut from recent history
// without re-pointing the camera.
//
// Discipline (spec §0 laws):
//   • consider() runs on MAIN (frame access) but only stamps references;
//     JPEG encode + depth copy happen on a dedicated serial queue behind a
//     single-flight latch — at most ONE frame's pixel buffers are retained
//     off-main at any moment (ARKit's buffer pool must never starve).
//   • bounded memory: ring capped by count AND total JPEG bytes; oldest out.
//   • keyframes PREFER sharp frames: motion-gated frames are skipped unless
//     the store has gone dry for a few seconds (pose coverage beats blur).
//
// Reads are lock-guarded value snapshots — safe from any thread/actor.

import Foundation
import ARKit
import CoreImage
import CoreVideo
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers
import simd

// MARK: - Keyframe

struct Keyframe {
    /// ARKit uptime clock — the same clock detections/identifications carry.
    let timestamp: TimeInterval
    let cameraTransform: simd_float4x4
    let intrinsics: simd_float3x3
    /// Captured-image pixel size (the space intrinsics + depth align to).
    let imageResolution: CGSize
    /// Compact scene-depth copy; nil when the frame carried none.
    let depth: DepthGrid?
    /// UPRIGHT JPEG (long edge ≤ KeyframeStore.jpegLongEdge) — the crop source.
    let jpeg: Data
    /// The rotation that made the JPEG upright from sensor-native.
    let orientation: CGImagePropertyOrientation
    /// MotionGate score at capture (0 = still) — crop-source preference.
    let motionScore: Float

    /// Project a world OBB into this keyframe's normalized sensor space.
    func projectRect(obb: OrientedBox) -> CGRect? {
        KeyframeGeometry.projectRect(obb: obb, cameraTransform: cameraTransform,
                                     intrinsics: intrinsics,
                                     imageResolution: imageResolution)
    }
}

// MARK: - Store

final class KeyframeStore: @unchecked Sendable {

    // MARK: Tunables

    /// Minimum spacing between stored keyframes (~2 Hz).
    static let minInterval: TimeInterval = 0.45
    /// After this long with nothing stored, accept even a motion-blurred frame —
    /// a blurry pose is still a pose.
    static let drySpellMax: TimeInterval = 3.0
    /// Ring caps: count AND total JPEG bytes (whichever trips first).
    static let maxCount = 120
    static let maxJPEGBytes = 40 * 1024 * 1024
    /// JPEG long edge (px). ARKit world-tracking capture is 1920×1440 — stored
    /// 1:1, i.e. 2× the linear resolution of the 960 px uplink stream.
    static let jpegLongEdge: CGFloat = 1920
    static let jpegQuality: CGFloat = 0.72

    // MARK: State

    private let lock = NSLock()
    private var ring: [Keyframe] = []
    private var jpegBytes = 0
    private var lastStoredAt: TimeInterval = -.greatestFiniteMagnitude
    private var encodeInFlight = false

    private let queue = DispatchQueue(label: "dev.spatail.perception.keyframes",
                                      qos: .utility)
    /// Created lazily ON the encode queue; never touched elsewhere.
    private var ciContext: CIContext?

    // MARK: Ingest (main)

    /// Offer the live frame. The store decides: throttled to ~2 Hz, skips
    /// motion-gated frames unless dry, single-flight on the encode side.
    func consider(frame: ARFrame, orientation: CGImagePropertyOrientation,
                  motion: MotionGate.Measure) {
        let now = frame.timestamp
        let proceed: Bool = {
            lock.lock(); defer { lock.unlock() }
            guard !encodeInFlight else { return false }
            guard now - lastStoredAt >= Self.minInterval else { return false }
            if motion.blocked, now - lastStoredAt < Self.drySpellMax { return false }
            encodeInFlight = true
            return true
        }()
        guard proceed else { return }

        // Stamp everything the queue side needs as VALUES + retained buffers.
        let captured = frame.capturedImage
        let sceneDepth = frame.smoothedSceneDepth ?? frame.sceneDepth
        let depthMap = sceneDepth?.depthMap
        let confidenceMap = sceneDepth?.confidenceMap
        let transform = frame.camera.transform
        let intrinsics = frame.camera.intrinsics
        let resolution = frame.camera.imageResolution
        let motionScore = motion.score

        queue.async { [weak self] in
            guard let self else { return }
            let depth = depthMap.flatMap { Self.copyDepth($0, confidence: confidenceMap) }
            let jpeg = self.encodeJPEG(captured, orientation: orientation)
            self.lock.lock()
            self.encodeInFlight = false
            if let jpeg {
                let kf = Keyframe(timestamp: now, cameraTransform: transform,
                                  intrinsics: intrinsics, imageResolution: resolution,
                                  depth: depth, jpeg: jpeg, orientation: orientation,
                                  motionScore: motionScore)
                self.ring.append(kf)
                self.jpegBytes += jpeg.count
                self.lastStoredAt = now
                while self.ring.count > Self.maxCount || self.jpegBytes > Self.maxJPEGBytes,
                      !self.ring.isEmpty {
                    self.jpegBytes -= self.ring.removeFirst().jpeg.count
                }
            }
            self.lock.unlock()
        }
    }

    // MARK: Reads (any thread)

    /// The keyframe nearest `timestamp` within `tolerance`, else nil.
    func nearest(to timestamp: TimeInterval,
                 tolerance: TimeInterval) -> Keyframe? {
        lock.lock(); defer { lock.unlock() }
        let best = ring.min { abs($0.timestamp - timestamp) < abs($1.timestamp - timestamp) }
        guard let best, abs(best.timestamp - timestamp) <= tolerance else { return nil }
        return best
    }

    /// Newest-first slice of recent keyframes.
    func recent(_ count: Int) -> [Keyframe] {
        lock.lock(); defer { lock.unlock() }
        return Array(ring.suffix(count).reversed())
    }

    var count: Int {
        lock.lock(); defer { lock.unlock() }
        return ring.count
    }

    // MARK: Crops (call from a background queue — decodes JPEG)

    /// One cut crop: the JPEG plus the normalized UPRIGHT rect it actually
    /// covers — the space a focus result's crop-relative boxes map back through.
    struct Crop {
        let jpeg: Data
        let uprightRect: CGRect
    }

    /// Cut an upright hi-res JPEG crop of a normalized SENSOR-space rect from a
    /// keyframe (margin expands the rect; output re-encoded at q0.8).
    /// nil when the crop lands off-image or is too small to be useful.
    static func cropJPEG(from keyframe: Keyframe, sensorRect: CGRect,
                         marginFraction: CGFloat = 0.15,
                         minCropSide: CGFloat = 48) -> Crop? {
        // Sensor → upright normalized space (the JPEG is stored upright).
        let upright = MaskProvider.uprightRect(fromSensor: sensorRect,
                                               orientation: keyframe.orientation)
        let expanded = upright.insetBy(dx: -upright.width * marginFraction,
                                       dy: -upright.height * marginFraction)
            .intersection(CGRect(x: 0, y: 0, width: 1, height: 1))
        guard !expanded.isNull, expanded.width > 0, expanded.height > 0 else { return nil }

        guard let source = CGImageSourceCreateWithData(keyframe.jpeg as CFData, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { return nil }
        let w = CGFloat(image.width), h = CGFloat(image.height)
        let pixelRect = CGRect(x: (expanded.minX * w).rounded(.down),
                               y: (expanded.minY * h).rounded(.down),
                               width: (expanded.width * w).rounded(.up),
                               height: (expanded.height * h).rounded(.up))
            .intersection(CGRect(x: 0, y: 0, width: w, height: h))
        guard pixelRect.width >= minCropSide, pixelRect.height >= minCropSide,
              let cropped = image.cropping(to: pixelRect) else { return nil }

        let out = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(
            out as CFMutableData, UTType.jpeg.identifier as CFString, 1, nil)
        else { return nil }
        CGImageDestinationAddImage(dest, cropped, [
            kCGImageDestinationLossyCompressionQuality: 0.8,
        ] as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { return nil }
        // The rect the PIXELS actually cover (post-rounding/clamping).
        let covered = CGRect(x: pixelRect.minX / w, y: pixelRect.minY / h,
                             width: pixelRect.width / w,
                             height: pixelRect.height / h)
        return Crop(jpeg: out as Data, uprightRect: covered)
    }

    // MARK: Encode side (queue-confined)

    private func encodeJPEG(_ pixelBuffer: CVPixelBuffer,
                            orientation: CGImagePropertyOrientation) -> Data? {
        let context: CIContext = {
            if let ciContext { return ciContext }
            let c = CIContext(options: [.cacheIntermediates: false])
            ciContext = c
            return c
        }()
        var image = CIImage(cvPixelBuffer: pixelBuffer).oriented(orientation)
        let longEdge = max(image.extent.width, image.extent.height)
        if longEdge > Self.jpegLongEdge {
            let s = Self.jpegLongEdge / longEdge
            image = image.transformed(by: CGAffineTransform(scaleX: s, y: s))
        }
        guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) else { return nil }
        return context.jpegRepresentation(
            of: image, colorSpace: colorSpace,
            options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption:
                        Self.jpegQuality])
    }

    /// Float32 depth (+ UInt8 confidence) buffers → compact DepthGrid values.
    private static func copyDepth(_ depthMap: CVPixelBuffer,
                                  confidence confidenceMap: CVPixelBuffer?) -> DepthGrid? {
        guard CVPixelBufferGetPixelFormatType(depthMap) == kCVPixelFormatType_DepthFloat32
        else { return nil }
        let w = CVPixelBufferGetWidth(depthMap)
        let h = CVPixelBufferGetHeight(depthMap)
        guard w > 0, h > 0 else { return nil }

        CVPixelBufferLockBaseAddress(depthMap, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(depthMap, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(depthMap) else { return nil }
        let rowBytes = CVPixelBufferGetBytesPerRow(depthMap)

        var depths = [Float16](repeating: 0, count: w * h)
        for y in 0..<h {
            let row = base.advanced(by: y * rowBytes).assumingMemoryBound(to: Float32.self)
            for x in 0..<w {
                let d = row[x]
                if d.isFinite, DepthGrid.depthRange.contains(d) {
                    depths[y * w + x] = Float16(d)
                }
            }
        }

        var confidences: [UInt8]?
        if let confidenceMap,
           CVPixelBufferGetWidth(confidenceMap) == w,
           CVPixelBufferGetHeight(confidenceMap) == h {
            CVPixelBufferLockBaseAddress(confidenceMap, .readOnly)
            defer { CVPixelBufferUnlockBaseAddress(confidenceMap, .readOnly) }
            if let cBase = CVPixelBufferGetBaseAddress(confidenceMap) {
                let cRowBytes = CVPixelBufferGetBytesPerRow(confidenceMap)
                var conf = [UInt8](repeating: 0, count: w * h)
                for y in 0..<h {
                    let row = cBase.advanced(by: y * cRowBytes)
                        .assumingMemoryBound(to: UInt8.self)
                    for x in 0..<w { conf[y * w + x] = row[x] }
                }
                confidences = conf
            }
        }
        return DepthGrid(width: w, height: h, depths: depths, confidences: confidences)
    }
}
