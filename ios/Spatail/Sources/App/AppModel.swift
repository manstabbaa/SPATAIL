import SwiftUI
import Combine
import ARKit

// AppModel — the composition root. Constructs every service and wires the seams:
//
//   hub → scanner                 (ARSessionDelegate multiplex)
//   hub → pipeline                (frame consumers, off-main detection at 1–2 Hz)
//   uplink.lastIdentification   → registry.applyIdentity (via the pipeline's
//                                  projector/resolver helpers — spec §3 identity attach)
//   uplink.lastExperienceDelta  → runtime.apply(delta:) gated on room-sent (spec §1.5)
//   scanner + registry          → uplink.sendRoom (initial on stream-up, then throttled)
//   runtime.onPlacementReport   → brainClient.postPlacementReport (spec §2.2)
//   AskBar → ask(_:)            → brainClient.generateModular → runtime.apply(contract:)
//
// Views do NOT observe through AppModel — SpatailApp injects each service as its
// own EnvironmentObject (nested ObservableObjects don't republish via a parent).

@MainActor
final class AppModel: ObservableObject {

    // MARK: Services (constructed once, dependency order)

    let settings: SettingsStore
    let hub: ARSessionHub
    let scanner: RoomScannerService
    let registry: ObjectRegistry
    let pipeline: LivePerceptionPipeline
    let uplink: VisionUplink
    let streamer: FrameStreamer
    let runtime: ExperienceRuntime
    private(set) var brainClient: BrainClient?

    // MARK: UI state

    /// Read by the Lens (W6) to draw the measured-vs-identified truth overlay.
    @Published var showTruthOverlay = false
    @Published var showLibrary = false
    @Published var showSettings = false

    enum AskState: Equatable {
        case idle
        case generating(prompt: String)
        case failed(message: String)
    }
    @Published private(set) var askState: AskState = .idle
    /// Session-local, newest first — surfaced by the Library sheet.
    @Published private(set) var askHistory: [String] = []

    private var cancellables: Set<AnyCancellable> = []
    private var started = false

    // MARK: Init + wiring

    init() {
        let settings = SettingsStore()
        let hub = ARSessionHub()
        let scanner = RoomScannerService()
        let registry = ObjectRegistry()
        let uplink = VisionUplink()

        self.settings = settings
        self.hub = hub
        self.scanner = scanner
        self.registry = registry
        self.uplink = uplink
        self.streamer = FrameStreamer(uplink: uplink)
        self.runtime = ExperienceRuntime()
        self.pipeline = LivePerceptionPipeline(hub: hub, registry: registry,
                                               surfacesProvider: { [weak scanner] in
                                                   scanner?.surfaces ?? []
                                               })

        hub.addSessionDelegate(scanner)
        brainClient = settings.endpoints.map(BrainClient.init)
        wire()
    }

    private func wire() {
        // Identity downlink → registry fusion (spec §3: project each object's OBB
        // into the identified frame, attach by IoU, debounce labels).
        uplink.$lastIdentification
            .compactMap { $0 }
            .receive(on: DispatchQueue.main)
            .sink { [weak self] identification in
                self?.apply(identification: identification)
            }
            .store(in: &cancellables)

        // Experience deltas → runtime, ignored until room.update has gone out on
        // THIS connection (spec §1.5), AND only while the user has an active ask.
        // Content appears because it was asked for (MATTER: "why it appears"
        // precedes appearance) — the live brain replans continuously, but an
        // unprompted plan renders nothing. The Truth Overlay still reads the
        // delta's fused/target directly off the uplink regardless.
        uplink.$lastExperienceDelta
            .compactMap { $0 }
            .receive(on: DispatchQueue.main)
            .sink { [weak self] delta in
                guard let self, self.uplink.hasSentRoomThisConnection,
                      self.liveConcept != nil else { return }
                self.runtime.apply(delta: delta, arView: self.hub.arView,
                                   surfaces: self.scanner.surfaces,
                                   objects: self.registry.objects)
            }
            .store(in: &cancellables)

        // Room snapshots: one as soon as the stream comes up, then throttled as
        // the scanner/registry evolve. Never per-frame (spec §1.1/§1.2).
        uplink.$state
            .removeDuplicates()
            .receive(on: DispatchQueue.main)
            .sink { [weak self] state in
                if state == .streaming { self?.sendRoomSnapshot() }
            }
            .store(in: &cancellables)

        Publishers.CombineLatest(scanner.$surfaces, registry.$objects)
            .throttle(for: .seconds(2), scheduler: DispatchQueue.main, latest: true)
            .sink { [weak self] _, _ in
                guard let self, self.uplink.state == .streaming else { return }
                self.sendRoomSnapshot()
            }
            .store(in: &cancellables)

        // Placement honesty: every applied plan reports back into the trace
        // (spec §2.2 — the Client column of /traces/view) AND feeds the Truth
        // Overlay's per-placement rationale lines.
        runtime.onPlacementReport = { [weak self] report in
            guard let self else { return }
            self.pipeline.debug.recordPlacementRationales(
                report.plan.placements.map(\.reason))
            guard let client = self.brainClient else { return }
            Task { await client.postPlacementReport(report) }
        }

        // Runtime asset streaming: server path → cached local file. The runtime
        // owns zero networking; this seam hands it the live BrainClient.
        runtime.assetResolver = { [weak self] path in
            guard let client = await self?.brainClient else {
                throw BrainClientError.badURL
            }
            return try await client.downloadAsset(relativePath: path)
        }

        // Placed experiences pin the registry objects they anchor to (spec §3:
        // pinned objects are exempt from the ~10 s expiry).
        runtime.onPinnedObjectsChanged = { [weak self] pinned in
            self?.registry.pinnedObjectIds = pinned
        }
    }

    // MARK: Lifecycle

    /// Called once from RootView.onAppear: perception on, brain link up.
    func start() {
        guard !started else { return }
        started = true
        pipeline.start()
        connectBrain()
    }

    /// (Re)connect the uplink + job client to the configured endpoints.
    /// Safe to call repeatedly (Settings' Connect button re-aims everything).
    func connectBrain() {
        guard let endpoints = settings.endpoints else { return }
        brainClient = BrainClient(endpoints: endpoints)
        streamer.stop()
        uplink.disconnect()
        uplink.connect(endpoints)
        // ~3 fps JPEG uplink, 2 Hz pose — the spec's ceilings (§1.1, law 3).
        let hub = self.hub
        streamer.start(frameProvider: { hub.currentFrame }, fps: 3, poseHz: 2)
    }

    func disconnectBrain() {
        streamer.stop()
        uplink.disconnect()
    }

    // MARK: Ask flow (AskBar → brain → runtime)

    /// Prompt → PC brain /modular → diff-based apply into the scene.
    func ask(_ prompt: String) {
        let trimmed = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        guard let client = brainClient ?? settings.endpoints.map(BrainClient.init) else {
            askState = .failed(message: "Set the PC brain address in Settings first.")
            return
        }
        brainClient = client
        askState = .generating(prompt: trimmed)
        // Most-recent-first, unique, bounded (law 3: no unbounded queues anywhere).
        askHistory.removeAll { $0 == trimmed }
        askHistory.insert(trimmed, at: 0)
        if askHistory.count > 24 { askHistory.removeLast(askHistory.count - 24) }
        // Scope the LIVE loop to this question too: the concept rides room.update
        // so the PC brain plans for what was actually asked (and can emit a
        // part-addressed target — the bottle-cap path). Also un-gates live
        // delta content, which never renders without an active ask.
        if uplink.state == .streaming {
            liveConcept = trimmed
            sendRoomSnapshot()
        }

        Task { [weak self] in
            do {
                let (contract, raw) = try await client.generateModularRaw(prompt: trimmed)
                guard let self else { return }
                // Persist verbatim (the library re-places against future rooms),
                // track the Meshy build when the server attached one, then
                // diff-apply into the scene.
                ExperienceLibraryStore.shared.record(prompt: trimmed,
                                                     contract: contract,
                                                     contractData: raw)
                if let jobId = contract.generationJobId, !jobId.isEmpty {
                    GenerationTracker.shared.track(jobId: jobId, client: client)
                }
                self.runtime.apply(contract: contract, raw: raw,
                                   arView: self.hub.arView,
                                   surfaces: self.scanner.surfaces,
                                   objects: self.registry.objects)
                self.askState = .idle
            } catch {
                self?.askState = .failed(message: error.localizedDescription)
            }
        }
    }

    func dismissAskFailure() {
        if case .failed = askState { askState = .idle }
    }

    func clearExperience() {
        runtime.clear()
        // Clearing content also retires the question: the next room.update goes
        // up without a concept, which clears it server-side too (spec §1.2).
        if liveConcept != nil {
            liveConcept = nil
            if uplink.state == .streaming { sendRoomSnapshot() }
        }
    }

    // MARK: Room uplink

    /// The user's active ask, riding every room.update while set. Live delta
    /// content is gated on this — nothing renders unprompted.
    @Published private(set) var liveConcept: String?

    private func sendRoomSnapshot() {
        // Pose rides its own 2 Hz channel via FrameStreamer; nil here is correct.
        uplink.sendRoom(surfaces: scanner.surfaces, objects: registry.objects,
                        pose: nil, concept: liveConcept)
    }

    // MARK: Identity fusion

    /// vision.identification → ObjectRegistry.applyIdentity (spec §1.3 + §3).
    private func apply(identification wire: IdentificationWire) {
        guard let primary = wire.primary, !primary.isEmpty else { return }
        // The wire's frameTimestamp is PC-epoch; the projector's pose ring and the
        // registry run on the ARKit uptime clock. The streamer kept a sent-frame
        // log exactly for this bridge (nearest sent frame wins).
        let ts = wire.frameTimestamp.flatMap { streamer.arTimestamp(nearestToEpoch: $0) }
            ?? hub.currentFrame?.timestamp
            ?? ProcessInfo.processInfo.systemUptime

        let detections: [Detection2D] = wire.detections.compactMap { d in
            guard let box = Self.normalizedRect(d.box) else { return nil }
            return Detection2D(label: d.label, confidence: Float(d.confidence),
                               box: box, source: .vlm, frameTimestamp: ts)
        }
        let parts: [(label: String, box: CGRect?, confidence: Float)] =
            (wire.parts ?? []).map { ($0.label, Self.normalizedRect($0.box), Float($0.confidence)) }

        let pipeline = self.pipeline
        registry.applyIdentity(primary: primary,
                               confidence: Float(wire.confidence ?? 0),
                               detections: detections,
                               parts: parts,
                               frameTimestamp: ts,
                               projector: pipeline.projector(for: ts),
                               resolver: { pipeline.resolvePartBox($0) })
    }

    /// Wire boxes are [x, y, w, h] normalized, origin top-left (spec §1.3).
    private static func normalizedRect(_ box: [Double]?) -> CGRect? {
        guard let b = box, b.count == 4 else { return nil }
        return CGRect(x: b[0], y: b[1], width: b[2], height: b[3])
    }
}
