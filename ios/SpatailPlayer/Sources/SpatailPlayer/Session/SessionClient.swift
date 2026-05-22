// SessionClient.swift
// WebSocket client for the live realtime session.
// Spec: docs/xr/REALTIME_PROTOCOL.md
//
// Uses URLSessionWebSocketTask — no third-party dependency.
// Re-emits decoded server events through an AsyncStream so the UI layer
// stays Combine/SwiftUI-friendly without coupling to a specific framework.

import Foundation

public actor SessionClient {

    public enum State: Sendable {
        case disconnected
        case connecting
        case ready(sessionId: String)
        case failed(Error)
    }

    public private(set) var state: State = .disconnected

    private let url: URL
    private let authToken: String?
    private let urlSession: URLSession
    private var task: URLSessionWebSocketTask?
    private var sendSeq: Int = 0
    private var receiveLoopTask: Task<Void, Never>?

    /// Stream of decoded server events. Consume from a single subscriber.
    public let events: AsyncStream<ServerEvent>
    private let eventsContinuation: AsyncStream<ServerEvent>.Continuation

    public init(url: URL, authToken: String? = nil,
                urlSession: URLSession = .shared) {
        self.url = url
        self.authToken = authToken
        self.urlSession = urlSession
        var continuation: AsyncStream<ServerEvent>.Continuation!
        self.events = AsyncStream { continuation = $0 }
        self.eventsContinuation = continuation
    }

    // MARK: - Lifecycle

    public func connect() async throws {
        state = .connecting
        var request = URLRequest(url: url)
        if let token = authToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let task = urlSession.webSocketTask(with: request)
        self.task = task
        task.resume()
        receiveLoopTask = Task { [weak self] in await self?.receiveLoop() }

        try await sendStart()
    }

    public func close(reason: String = "user_left") async {
        try? await send(type: OutboundEventType.sessionEnd,
                        payload: SessionEndPayload(reason: reason))
        receiveLoopTask?.cancel()
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        state = .disconnected
    }

    // MARK: - High-level senders

    public func prompt(_ text: String,
                       previousExperienceId: String? = nil) async throws {
        try await send(
            type: .userPrompt,
            payload: UserPromptPayload(
                text: text,
                audioUrl: nil,
                context: UserPromptPayload.Context(
                    previousExperienceId: previousExperienceId)))
    }

    public func roomUpdate(_ payload: RoomUpdatePayload) async throws {
        try await send(type: .roomUpdate, payload: payload)
    }

    public func poseUpdate(_ payload: PoseUpdatePayload) async throws {
        try await send(type: .poseUpdate, payload: payload)
    }

    public func tap(elementId: String, world: SIMD3<Float>? = nil) async throws {
        try await send(
            type: .interactionTap,
            payload: InteractionTapPayload(
                elementId: elementId,
                tapWorld: world.map { [$0.x, $0.y, $0.z] }))
    }

    public func resync(knownVersion: Int) async throws {
        try await send(
            type: .sessionResync,
            payload: SessionResyncPayload(knownVersion: knownVersion))
    }

    // MARK: - Wire send

    private func sendStart() async throws {
        let payload = SessionStartPayload(
            client: .init(
                platform: "ios",
                appVersion: Bundle.main
                    .object(forInfoDictionaryKey: "CFBundleShortVersionString")
                    as? String ?? "0.1.0",
                osVersion: ProcessInfo.processInfo.operatingSystemVersionString),
            capabilities: .init(
                arkitVersion: "6",
                hasLidar: false,
                supportsRoomCapture: true,
                supportsRealityKit2: true),
            supportedBundleSchemaVersions: BundleManifest.supportedSchemaVersions)
        try await send(type: .sessionStart, payload: payload)
    }

    private func send<P: Codable>(type: OutboundEventType, payload: P) async throws {
        guard let task else { throw URLError(.notConnectedToInternet) }
        sendSeq += 1
        let envelope = SessionEnvelope(type: type.rawValue,
                                        seq: sendSeq,
                                        payload: payload)
        let data = try JSONEncoder().encode(envelope)
        guard let string = String(data: data, encoding: .utf8) else { return }
        try await task.send(.string(string))
    }

    // MARK: - Wire receive

    private func receiveLoop() async {
        while !Task.isCancelled, let task else { break }
        guard let task else { return }
        do {
            let msg = try await task.receive()
            await handle(msg)
            await receiveLoop()  // tail-call style
        } catch {
            state = .failed(error)
            eventsContinuation.finish()
        }
    }

    private func handle(_ msg: URLSessionWebSocketTask.Message) async {
        let data: Data
        switch msg {
        case .string(let s): data = Data(s.utf8)
        case .data(let d):   data = d
        @unknown default:    return
        }

        // Peek at the envelope header to choose the payload type.
        struct Header: Decodable { let type: String }
        guard let header = try? JSONDecoder().decode(Header.self, from: data),
              let kind = InboundEventType(rawValue: header.type) else {
            return
        }

        let decoder = JSONDecoder()
        do {
            switch kind {
            case .sessionReady:
                let env = try decoder.decode(
                    SessionEnvelope<SessionReadyPayload>.self, from: data)
                state = .ready(sessionId: env.payload.sessionId)
                eventsContinuation.yield(.sessionReady(env.payload))

            case .understandingPartial:
                let env = try decoder.decode(
                    SessionEnvelope<UnderstandingPartialPayload>.self, from: data)
                eventsContinuation.yield(.understandingPartial(env.payload))

            case .assetUrl:
                let env = try decoder.decode(
                    SessionEnvelope<AssetUrlPayload>.self, from: data)
                eventsContinuation.yield(.assetUrl(env.payload))

            case .experienceDelta:
                // Discriminate full vs patch by `kind` field.
                struct Probe: Decodable { let payload: KindOnly
                    struct KindOnly: Decodable { let kind: String } }
                let probe = try decoder.decode(Probe.self, from: data)
                if probe.payload.kind == "full" {
                    let env = try decoder.decode(
                        SessionEnvelope<ExperienceFullPayload>.self, from: data)
                    eventsContinuation.yield(.experienceFull(env.payload))
                } else {
                    let env = try decoder.decode(
                        SessionEnvelope<ExperiencePatchPayload>.self, from: data)
                    eventsContinuation.yield(.experiencePatch(env.payload))
                }

            case .narrationChunk:
                let env = try decoder.decode(
                    SessionEnvelope<NarrationChunkPayload>.self, from: data)
                eventsContinuation.yield(.narrationChunk(env.payload))

            case .error:
                let env = try decoder.decode(
                    SessionEnvelope<ErrorPayload>.self, from: data)
                eventsContinuation.yield(.error(env.payload))
            }
        } catch {
            // Swallow decode errors but keep the loop alive.
            // TODO: surface to a diagnostics channel.
        }
    }
}
