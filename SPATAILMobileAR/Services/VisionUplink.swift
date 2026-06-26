// VisionUplink.swift
//
// The phone half of the SPATAIL TRACKED stream: capture the rear camera,
// downscale + JPEG-encode frames, stream them to the PC "live intelligence
// engine" (pipeline/server/spatail_vision_engine.py) over a binary WebSocket,
// and surface the VLM's identifications back into SwiftUI.
//
// Wire contract: docs/xr/REALTIME_PROTOCOL.md §10.
//   up   : raw binary WebSocket messages, each a JPEG (~640px, ~3 fps)
//   down : { "type": "vision.identification", "payload": { primary, detections, ... } }
//
// Latency split (the design rule): the PC VLM only answers "what + roughly
// where" at a few Hz. Continuous pose stays on-device (ARKit), so a ~1s VLM
// round-trip is fine — we never put per-frame tracking on the network.

import Foundation
import AVFoundation
import CoreImage
import UIKit

// MARK: - Wire models (decode the engine's downlink)

struct VisionIdentification: Decodable, Equatable {
    struct Detection: Decodable, Equatable, Identifiable {
        let label: String
        let confidence: Double
        let box: [Double]?          // normalized [x, y, w, h], origin top-left, or nil
        var id: String { label }
    }
    let primary: String?
    let detections: [Detection]
    let rawText: String?
    let latencyMs: Int?
    let model: String?
}

private struct VisionMessage: Decodable {
    let type: String
    let payload: VisionIdentification
}

// MARK: - Frame sink

/// One-way JPEG sink over a WebSocket. `URLSessionWebSocketTask.send` is safe to
/// call from any thread, so the camera queue can push frames straight through
/// without hopping to the main actor. Marked `@unchecked Sendable` for that reason.
final class FrameSender: @unchecked Sendable {
    private let task: URLSessionWebSocketTask
    private let onError: @Sendable (Error) -> Void

    init(task: URLSessionWebSocketTask, onError: @escaping @Sendable (Error) -> Void) {
        self.task = task
        self.onError = onError
    }

    func send(_ jpeg: Data) {
        task.send(.data(jpeg)) { [onError] err in
            if let err { onError(err) }
        }
    }
}

// MARK: - Camera → JPEG frames

/// Owns an `AVCaptureSession`, emits downscaled JPEG frames on `onFrame` from a
/// background queue. Not main-actor: capture callbacks are inherently off-main.
/// `@unchecked Sendable` because we control the access pattern (onFrame is set
/// before streaming begins and the rest is touched only on `queue`).
final class CameraFrameStreamer: NSObject, @unchecked Sendable,
                                  AVCaptureVideoDataOutputSampleBufferDelegate {
    let session = AVCaptureSession()
    private let queue = DispatchQueue(label: "com.spatail.vision.frames")
    private let ciContext = CIContext(options: [.useSoftwareRenderer: false])
    private let output = AVCaptureVideoDataOutput()

    private var lastSent = Date.distantPast
    var minInterval: TimeInterval = 0.33     // ~3 fps uplink
    var maxDimension: CGFloat = 640
    var jpegQuality: CGFloat = 0.5

    /// Called on the capture queue with a ready-to-send JPEG. Set before `start()`.
    var onFrame: (@Sendable (Data) -> Void)?

    func configure() {
        queue.async {
            self.session.beginConfiguration()
            self.session.sessionPreset = .hd1280x720
            if let device = AVCaptureDevice.default(.builtInWideAngleCamera,
                                                    for: .video, position: .back),
               let input = try? AVCaptureDeviceInput(device: device),
               self.session.canAddInput(input) {
                self.session.addInput(input)
            }
            self.output.videoSettings = [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
            ]
            self.output.alwaysDiscardsLateVideoFrames = true
            self.output.setSampleBufferDelegate(self, queue: self.queue)
            if self.session.canAddOutput(self.output) {
                self.session.addOutput(self.output)
            }
            self.session.commitConfiguration()
        }
    }

    func start() { queue.async { if !self.session.isRunning { self.session.startRunning() } } }
    func stop()  { queue.async { if self.session.isRunning { self.session.stopRunning() } } }

    nonisolated func captureOutput(_ output: AVCaptureOutput,
                                   didOutput sampleBuffer: CMSampleBuffer,
                                   from connection: AVCaptureConnection) {
        let now = Date()
        guard now.timeIntervalSince(lastSent) >= minInterval else { return }
        lastSent = now
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }

        // Rear camera buffers are landscape-right; .right orients to portrait.
        var image = CIImage(cvPixelBuffer: pixelBuffer).oriented(.right)
        let longEdge = max(image.extent.width, image.extent.height)
        if longEdge > maxDimension {
            let s = maxDimension / longEdge
            image = image.transformed(by: CGAffineTransform(scaleX: s, y: s))
        }
        let opts: [CIImageRepresentationOption: Any] =
            [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: jpegQuality]
        guard let data = ciContext.jpegRepresentation(
            of: image, colorSpace: CGColorSpaceCreateDeviceRGB(), options: opts) else { return }
        onFrame?(data)
    }
}

// MARK: - Live vision model (the SwiftUI-facing controller)

@MainActor
final class LiveVisionModel: ObservableObject {
    enum State: Equatable {
        case idle, connecting, streaming, failed(String)
    }

    @Published var serverHost: String = UserDefaults.standard
        .string(forKey: "spatail.visionHost") ?? "192.168.1.50:8798"
    @Published private(set) var state: State = .idle
    @Published private(set) var lastIdentification: VisionIdentification?
    @Published private(set) var framesSent: Int = 0

    let streamer = CameraFrameStreamer()
    private var task: URLSessionWebSocketTask?
    private let urlSession = URLSession(configuration: .default)
    private var frameCount = 0

    /// Ask for camera access and start the preview (no network yet).
    func startPreview() async {
        let granted = await AVCaptureDevice.requestAccess(for: .video)
        guard granted else { state = .failed("Camera access denied"); return }
        streamer.configure()
        streamer.start()
    }

    func connect() {
        UserDefaults.standard.set(serverHost, forKey: "spatail.visionHost")
        guard let url = URL(string: "ws://\(serverHost)/v1/vision") else {
            state = .failed("Bad host"); return
        }
        state = .connecting
        let task = urlSession.webSocketTask(with: url)
        self.task = task
        task.resume()

        let sender = FrameSender(task: task) { _ in /* transient send errors are non-fatal */ }
        streamer.onFrame = { [weak self] jpeg in
            sender.send(jpeg)
            Task { @MainActor in self?.tickFrame() }
        }
        state = .streaming
        receiveLoop()
    }

    func disconnect() {
        streamer.onFrame = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        state = .idle
    }

    func stopAll() {
        disconnect()
        streamer.stop()
    }

    private func tickFrame() {
        frameCount += 1
        // surface every 5th frame to avoid thrashing @Published at 3 fps
        if frameCount % 5 == 0 { framesSent = frameCount }
    }

    private func receiveLoop() {
        guard let task else { return }
        Task { [weak self] in
            while true {
                do {
                    let message = try await task.receive()
                    guard let self else { return }
                    switch message {
                    case .string(let text): self.handle(text)
                    case .data(let data):
                        if let text = String(data: data, encoding: .utf8) { self.handle(text) }
                    @unknown default: break
                    }
                } catch {
                    self?.state = .failed(error.localizedDescription)
                    return
                }
            }
        }
    }

    private func handle(_ text: String) {
        guard let data = text.data(using: .utf8),
              let msg = try? JSONDecoder().decode(VisionMessage.self, from: data),
              msg.type == "vision.identification" else { return }
        lastIdentification = msg.payload
    }
}
