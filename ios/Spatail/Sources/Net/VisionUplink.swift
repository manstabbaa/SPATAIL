// VisionUplink.swift — the phone half of the SPATAIL live vision link, REWRITTEN
// around URLSessionWebSocketTask per LIVE_BRAIN_SPEC §0 laws + §1.
//
// One socket, two directions:
//   up   : binary JPEG frames (≤1 in flight, drop-on-busy — law 3) +
//          JSON text control (room.update, pose.update)
//   down : JSON text (vision.identification, experience.delta)
//
// Diagnosed fixes over the SPATAILMobileAR lineage, all applied here:
//   • honest connection state — N (5) consecutive send failures flip
//     `.failed(consecutiveFailures:)`; the UI never lies about streaming
//   • deliberate disconnect() never surfaces as .failed (connection epochs
//     filter every stale callback: sends, receive loop, close events)
//   • detection identity is positional, never label-keyed (WireMessages)
//   • the §1.5 delta gate is real: `hasSentRoomThisConnection` set in
//     sendRoom, cleared on connect/disconnect; AppModel drops deltas until it
//   • .streaming means the socket actually OPENED (URLSessionWebSocketDelegate
//     didOpen), not "we called resume()"

import Foundation
import ARKit
import UIKit

// MARK: - Thread-safe box (shared by Net — uplink sender handoff, streamer state)

/// Minimal lock box so `nonisolated` senders and background encode queues can
/// share state with a @MainActor owner without data races.
final class NetBox<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: Value

    init(_ value: Value) { stored = value }

    var value: Value {
        lock.lock(); defer { lock.unlock() }
        return stored
    }

    func set(_ value: Value) {
        lock.lock(); defer { lock.unlock() }
        stored = value
    }

    @discardableResult
    func withLock<R>(_ body: (inout Value) -> R) -> R {
        lock.lock(); defer { lock.unlock() }
        return body(&stored)
    }
}

// MARK: - FrameSender (per-connection; backpressure + honest failure counting)

/// Owns the send side of one WebSocket connection. Callable from ANY thread
/// (URLSessionWebSocketTask.send is thread-safe). Enforces spec law 3: at most
/// ONE frame in flight, drop-on-busy (latest-wins), and a consecutive-failure
/// count reported after every send so the owner can flip state honestly.
final class FrameSender: @unchecked Sendable {
    private let task: URLSessionWebSocketTask
    private let lock = NSLock()
    private var framesInFlight = 0
    private var consecutiveFailures = 0
    private var framesSent = 0
    private var framesDropped = 0
    /// Called after EVERY send completion with the consecutive-failure count
    /// (0 = this send succeeded). May be invoked on any thread.
    private let onSendResult: @Sendable (Int) -> Void

    init(task: URLSessionWebSocketTask, onSendResult: @escaping @Sendable (Int) -> Void) {
        self.task = task
        self.onSendResult = onSendResult
    }

    var currentConsecutiveFailures: Int {
        lock.lock(); defer { lock.unlock() }
        return consecutiveFailures
    }

    var stats: (sent: Int, dropped: Int) {
        lock.lock(); defer { lock.unlock() }
        return (framesSent, framesDropped)
    }

    /// Binary JPEG uplink — capped at 1 in flight; busy frames are DROPPED,
    /// never queued (no unbounded queues anywhere — law 2/3).
    func sendFrame(_ jpeg: Data) {
        let proceed: Bool = {
            lock.lock(); defer { lock.unlock() }
            if framesInFlight >= 1 { framesDropped += 1; return false }
            framesInFlight += 1
            return true
        }()
        guard proceed else { return }
        task.send(.data(jpeg)) { [self] error in
            let count: Int = {
                lock.lock(); defer { lock.unlock() }
                framesInFlight -= 1
                if error == nil {
                    framesSent += 1
                    consecutiveFailures = 0
                } else {
                    consecutiveFailures += 1
                }
                return consecutiveFailures
            }()
            onSendResult(count)
        }
    }

    /// Text control channel (room.update / pose.update). Small + rare, so no
    /// in-flight cap — but failures still count: a sick link is a sick link.
    func sendText(_ text: String) {
        task.send(.string(text)) { [self] error in
            let count: Int = {
                lock.lock(); defer { lock.unlock() }
                if error == nil { consecutiveFailures = 0 }
                else { consecutiveFailures += 1 }
                return consecutiveFailures
            }()
            onSendResult(count)
        }
    }
}

// MARK: - Socket open/close delegate (weak proxy — no retain cycle w/ URLSession)

private final class VisionSocketDelegate: NSObject, URLSessionWebSocketDelegate {
    weak var owner: VisionUplink?

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                    didOpenWithProtocol protocol: String?) {
        Task { @MainActor [weak self] in self?.owner?.socketDidOpen(webSocketTask) }
    }

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                    didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
                    reason: Data?) {
        Task { @MainActor [weak self] in self?.owner?.socketDidClose(webSocketTask) }
    }
}

// MARK: - VisionUplink

@MainActor
final class VisionUplink: NSObject, ObservableObject {

    /// Spec law 3: N consecutive send failures flip the state to .failed.
    static let failureThreshold = 5

    @Published private(set) var state: LinkState = .idle
    @Published private(set) var lastIdentification: IdentificationWire?
    @Published private(set) var lastExperienceDelta: ExperienceDeltaWire?

    /// The §1.5 delta gate: experience.delta is meaningless until the brain
    /// has THIS connection's room. Set in sendRoom, cleared on connect/disconnect.
    private(set) var hasSentRoomThisConnection = false

    /// Uplink frame counters for the truth overlay / settings HUD.
    var frameStats: (sent: Int, dropped: Int) { senderBox.value?.stats ?? (0, 0) }

    // Connection identity. Every async callback carries the epoch it was born
    // under; a mismatch means connect()/disconnect() happened since — the
    // callback is from a dead connection and must not touch state. This is
    // what keeps a deliberate disconnect from ever surfacing as .failed.
    private var epoch = 0
    private var socket: URLSessionWebSocketTask?
    private let senderBox = NetBox<FrameSender?>(nil)
    private let socketDelegate = VisionSocketDelegate()
    private var urlSession: URLSession?
    /// True once the receive loop (or the close delegate) declared this
    /// connection dead — blocks the send-success → .streaming recovery path.
    private var receiveSideDown = false

    /// One room identity per app session — the same ARKit world the scanner
    /// measures in. (Coordinates are per-session; the server scopes the room
    /// to the connection that sent it.)
    private let roomId = "room-" + UUID().uuidString.lowercased()

    // Main-actor only (handleText / sendRoom run on main).
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()

    override init() {
        super.init()
        socketDelegate.owner = self
    }

    // MARK: Connect / disconnect

    func connect(_ endpoints: BrainEndpoints) {
        teardownSocket()
        epoch += 1
        let myEpoch = epoch
        hasSentRoomThisConnection = false
        receiveSideDown = false
        lastIdentification = nil
        lastExperienceDelta = nil
        state = .connecting

        let session = ensureSession()
        let task = session.webSocketTask(with: endpoints.visionSocketURL)
        socket = task
        let sender = FrameSender(task: task) { [weak self] failures in
            Task { @MainActor in
                self?.handleSendResult(consecutiveFailures: failures, epoch: myEpoch)
            }
        }
        senderBox.set(sender)
        task.resume()
        receiveLoop(on: task, epoch: myEpoch)
    }

    /// Deliberate teardown — never surfaces as .failed (epoch bump invalidates
    /// every callback the dying connection still owes us).
    func disconnect() {
        teardownSocket()
        epoch += 1
        hasSentRoomThisConnection = false
        state = .idle
    }

    private func teardownSocket() {
        senderBox.set(nil)
        let task = socket
        socket = nil          // nil BEFORE cancel so didClose(task) no longer matches
        task?.cancel(with: .goingAway, reason: nil)
    }

    private func ensureSession() -> URLSession {
        if let urlSession { return urlSession }
        let session = URLSession(configuration: .default,
                                 delegate: socketDelegate, delegateQueue: nil)
        urlSession = session
        return session
    }

    // MARK: Uplink — frames + pose (callable from any thread; hot paths never hop)

    /// JPEG camera frame. Called from FrameStreamer's encode queue — the data
    /// goes straight to the socket through the sender box, no main-actor hop.
    nonisolated func sendFrame(_ jpeg: Data) {
        senderBox.value?.sendFrame(jpeg)
    }

    /// pose.update at ≤ 2 Hz (spec §1.1) — keeps the brain's gaze ray live
    /// between room sends. Never triggers a replan server-side. Callable off
    /// main (FrameStreamer emits these), so it encodes with a local encoder.
    nonisolated func sendPose(_ pose: PoseWire) {
        guard let sender = senderBox.value else { return }
        guard let data = try? JSONEncoder().encode(PoseUpdateEnvelope(payload: pose)),
              let text = String(data: data, encoding: .utf8) else { return }
        sender.sendText(text)
    }

    // MARK: Uplink — room.update (spec §1.2)

    /// Send the current room truth: merged surfaces + tracked objects (+ pose,
    /// when the caller isn't already running the 2 Hz pose channel). Arms the
    /// §1.5 delta gate for this connection.
    func sendRoom(surfaces: [RoomSurface], objects: [SpatailObject], pose: PoseWire?) {
        guard let sender = senderBox.value else { return }
        let room = RoomContractWire(surfaces: surfaces, objects: objects,
                                    roomId: roomId,
                                    device: UIDevice.current.model,
                                    lidar: Self.lidarAvailable)
        let envelope = RoomUpdateEnvelope(payload: .init(room: room, pose: pose))
        do {
            let data = try encoder.encode(envelope)
            guard let text = String(data: data, encoding: .utf8) else { return }
            sender.sendText(text)
            hasSentRoomThisConnection = true
        } catch {
            // Encoding our own snapshot failing means bad geometry (non-finite
            // floats), not a bad link — log loudly, don't flip the state.
            print("[VisionUplink] room.update encode failed: \(error)")
        }
    }

    private static let lidarAvailable =
        ARWorldTrackingConfiguration.supportsSceneReconstruction(.meshWithClassification)

    // MARK: Downlink — receive loop (vision.identification, experience.delta)

    private func receiveLoop(on task: URLSessionWebSocketTask, epoch: Int) {
        Task { [weak self] in
            while true {
                do {
                    let message = try await task.receive()
                    guard let self, self.epoch == epoch else { return }
                    self.handle(message)
                } catch {
                    guard let self, self.epoch == epoch else { return }  // stale = deliberate
                    self.linkDown(reason: error.localizedDescription)
                    return
                }
            }
        }
    }

    private func handle(_ message: URLSessionWebSocketTask.Message) {
        switch message {
        case .string(let text):
            handleText(text)
        case .data(let data):
            if let text = String(data: data, encoding: .utf8) { handleText(text) }
        @unknown default:
            break
        }
    }

    private func handleText(_ text: String) {
        guard let data = text.data(using: .utf8),
              let header = try? decoder.decode(WireEnvelopeHeader.self, from: data)
        else { return }
        switch header.type {
        case "vision.identification":
            if let env = try? decoder.decode(WireEnvelope<IdentificationWire>.self,
                                             from: data) {
                lastIdentification = env.payload
            }
        case "experience.delta":
            if let env = try? decoder.decode(WireEnvelope<ExperienceDeltaWire>.self,
                                             from: data) {
                if env.payload.experience == nil {
                    print("[VisionUplink] experience.delta v\(env.payload.version) " +
                          "arrived without a decodable experience — dropped downstream")
                }
                lastExperienceDelta = env.payload
            }
        default:
            break   // future message types are additive; ignore, don't die
        }
    }

    // MARK: Honest state transitions

    fileprivate func socketDidOpen(_ task: URLSessionWebSocketTask) {
        guard task === socket else { return }             // stale connection
        if state == .connecting { state = .streaming }
    }

    fileprivate func socketDidClose(_ task: URLSessionWebSocketTask) {
        guard task === socket else { return }             // deliberate close already nils it
        linkDown(reason: "socket closed by peer")
    }

    private func handleSendResult(consecutiveFailures: Int, epoch: Int) {
        guard epoch == self.epoch else { return }         // dead connection's callback
        if consecutiveFailures >= Self.failureThreshold {
            if state != .failed(consecutiveFailures: consecutiveFailures) {
                state = .failed(consecutiveFailures: consecutiveFailures)
            }
        } else if consecutiveFailures == 0, case .failed = state, !receiveSideDown {
            // Sends are landing again on the SAME socket (and the receive loop
            // never died) — a transient stall recovered. Honest either way.
            state = .streaming
        }
    }

    private func linkDown(reason: String) {
        receiveSideDown = true
        guard state == .streaming || state == .connecting else { return }
        let failures = max(1, senderBox.value?.currentConsecutiveFailures ?? 1)
        state = .failed(consecutiveFailures: failures)
        print("[VisionUplink] link down: \(reason)")
    }
}
