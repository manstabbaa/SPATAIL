// FrameStreamer.swift — pulls ARFrames from the hub on the MAIN actor (frame
// refs only), then does everything heavy OFF main (spec law 2):
//
//   main (fps tick)     : grab ARFrame → keep CVPixelBuffer + camera transform
//   encode queue        : orient(.right) → scale ~960px long edge → JPEG q0.6
//   uplink (any thread) : sendFrame (1-in-flight cap lives in FrameSender)
//
// A single-flight latch is taken BEFORE dispatching to the encode queue —
// if an encode is still running, this tick's frame is dropped (latest-wins,
// no queue growth). The encode path is the proven SPATAILMobileAR
// CameraFrameStreamer pattern (CIImage .oriented(.right) → jpegRepresentation),
// retuned to the Live Brain contract (960px / 0.6 vs the old 640px / 0.5).
//
// The same tick also emits pose.update at poseHz (≤ 2 Hz — spec §1.1) from the
// frame's camera transform: position = translation, forward = -Z, up = +Y.

import Foundation
import ARKit
import CoreImage
import CoreGraphics
import ImageIO

/// @unchecked Sendable: all mutable state lives in NetBoxes; CIContext is
/// thread-safe; the uplink is @MainActor (Sendable).
final class FrameStreamer: @unchecked Sendable {

    /// Single-consumer handoff of a CVPixelBuffer from the main-actor grab to
    /// the encode queue (ARKit hands ownership; nothing else touches it).
    private struct PixelHandoff: @unchecked Sendable {
        let buffer: CVPixelBuffer
    }

    private let uplink: VisionUplink
    private let encodeQueue = DispatchQueue(label: "com.spatail.net.frame-encode",
                                            qos: .userInitiated)
    private let ciContext = CIContext(options: [.useSoftwareRenderer: false])

    /// Live Brain contract encode parameters.
    private let maxLongEdge: CGFloat = 960
    private let jpegQuality: CGFloat = 0.6

    /// Single-flight encode latch (latched on main BEFORE dispatch — law 2).
    private let encodeBusy = NetBox(false)
    private let loop = NetBox<Task<Void, Never>?>(nil)
    /// Ring of (wall-clock send time → ARKit frame timestamp) for the last 32
    /// sent frames — best-effort bridge from the engine's `frameTimestamp`
    /// (PC ingest epoch) back to an on-device frame time. Clock skew between
    /// PC and phone applies; treat as a hint, not ground truth.
    private let sentLog = NetBox<[(epoch: Double, arTimestamp: Double)]>([])

    init(uplink: VisionUplink) {
        self.uplink = uplink
    }

    /// Start pulling frames at `fps` (JPEG uplink) and emitting pose.update at
    /// `poseHz`. `frameProvider` runs on the main actor — hand it the hub's
    /// `currentFrame`. Safe to call repeatedly; restarts the loop.
    func start(frameProvider: @escaping @MainActor () -> ARFrame?,
               fps: Double, poseHz: Double) {
        stop()
        let frameInterval = 1.0 / max(fps, 0.1)
        let poseInterval = 1.0 / max(poseHz, 0.01)
        let task = Task { @MainActor [weak self] in
            var lastPoseAt: Double = 0
            while !Task.isCancelled {
                if let self, let frame = frameProvider() {
                    self.tick(frame: frame, poseInterval: poseInterval,
                              lastPoseAt: &lastPoseAt)
                }
                try? await Task.sleep(nanoseconds: UInt64(frameInterval * 1_000_000_000))
            }
        }
        loop.set(task)
    }

    func stop() {
        loop.withLock { task in
            task?.cancel()
            task = nil
        }
    }

    /// Map a wall-clock timestamp (e.g. the brain's identification
    /// `frameTimestamp`) to the ARKit timestamp of the nearest frame we sent.
    /// nil until a frame has gone out.
    func arTimestamp(nearestToEpoch epoch: Double) -> Double? {
        sentLog.value.min(by: { abs($0.epoch - epoch) < abs($1.epoch - epoch) })?
            .arTimestamp
    }

    // MARK: - One tick (main actor: refs only, zero pixel work)

    @MainActor
    private func tick(frame: ARFrame, poseInterval: Double, lastPoseAt: inout Double) {
        let nowEpoch = Date().timeIntervalSince1970

        // pose.update at poseHz — tiny JSON, built from this same frame.
        if nowEpoch - lastPoseAt >= poseInterval {
            lastPoseAt = nowEpoch
            uplink.sendPose(PoseWire(cameraTransform: frame.camera.transform,
                                     timestamp: nowEpoch))
        }

        // Single-flight: latch BEFORE dispatch; busy → drop this frame.
        let latched = encodeBusy.withLock { busy -> Bool in
            if busy { return false }
            busy = true
            return true
        }
        guard latched else { return }

        // Keep only what the encode needs (retaining whole ARFrames starves
        // ARKit's buffer pool) — the pixel buffer and the frame's timestamp.
        let handoff = PixelHandoff(buffer: frame.capturedImage)
        let arTimestamp = frame.timestamp

        encodeQueue.async { [weak self] in
            guard let self else { return }
            defer { self.encodeBusy.set(false) }
            guard let jpeg = self.encodeJPEG(handoff.buffer) else { return }
            self.recordSent(epoch: Date().timeIntervalSince1970,
                            arTimestamp: arTimestamp)
            self.uplink.sendFrame(jpeg)
        }
    }

    // MARK: - Encode (background queue only)

    private func encodeJPEG(_ pixelBuffer: CVPixelBuffer) -> Data? {
        // ARKit capture buffers are sensor-landscape; .right orients portrait —
        // the SAME image space the VLM's detection boxes come back in.
        var image = CIImage(cvPixelBuffer: pixelBuffer).oriented(.right)
        let longEdge = max(image.extent.width, image.extent.height)
        if longEdge > maxLongEdge {
            let s = maxLongEdge / longEdge
            image = image.transformed(by: CGAffineTransform(scaleX: s, y: s))
        }
        let opts: [CIImageRepresentationOption: Any] = [
            kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption:
                jpegQuality
        ]
        return ciContext.jpegRepresentation(of: image,
                                            colorSpace: CGColorSpaceCreateDeviceRGB(),
                                            options: opts)
    }

    private func recordSent(epoch: Double, arTimestamp: Double) {
        sentLog.withLock { log in
            log.append((epoch: epoch, arTimestamp: arTimestamp))
            if log.count > 32 { log.removeFirst(log.count - 32) }
        }
    }
}
