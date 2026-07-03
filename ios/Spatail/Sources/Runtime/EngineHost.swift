// EngineHost.swift — SpatailEngine (the platform-agnostic game core) wired into
// the app through its RealityKit backend adapter.
//
// When a /modular response carries engine payloads — the v0.8 engine-contract
// shape: entities/templates + rules + objectives (ExplainerKit/ShooterKit and
// friends) — this host decodes them TOLERANTLY from the same raw bytes, spins up
// `SpatailEngine` + `RealityKitBackend`, parents the backend's root under the
// runtime's solved placement root, and drives the loop:
//
//   host events in  : tick (per frame), input.tap (ARView hit-test),
//                     input.fire (shooter fire control), collisions (RealityKit)
//   commands out    : createVisual / playClip / applyEffect / physics /
//                     speak / playSound / hud — all applied by the backend
//
// Asset discipline: the backend's `resolveAssetURL` seam is synchronous, so the
// host PREFETCHES every contract asset through the runtime's async resolver
// (BrainClient.downloadAsset, off-main) BEFORE calling engine.load — by load
// time the synchronous cache probe (BrainClient.cachedAssetURL) hits and the
// backend loads local files only. Anything that failed to download falls back
// to the contract's primitive — placeholder-then-nothing-worse, never a stall.
//
// Wire contracts that carry NO engine payloads simply produce a nil host —
// zero behavior change. Brain-side adoption needs zero app changes.

import Foundation
import RealityKit
import Combine
import simd
import SpatailEngineCore
import SpatailEngineRealityKit

@MainActor
final class EngineHost {

    /// Engine HUD snapshot for the glass chrome (score / objective / outcome).
    struct HUDState: Equatable {
        var genre: String
        var fields: [(key: String, value: String)]
        static func == (a: HUDState, b: HUDState) -> Bool {
            a.genre == b.genre && a.fields.map(\.key) == b.fields.map(\.key)
                && a.fields.map(\.value) == b.fields.map(\.value)
        }
    }

    let engine: SpatailEngine
    let backend: RealityKitBackend
    let genre: String
    private let contract: SpatailEngineCore.ExperienceContract
    private var collisionSub: Cancellable?
    private var loaded = false

    /// HUD updates for the SwiftUI layer (score, objective, win/lose).
    var onHUD: ((HUDState) -> Void)?

    // MARK: detection (tolerant — this is the "zero app changes" seam)

    /// Decode an engine contract out of the raw /modular bytes IF they carry
    /// engine payloads. Accepts the payload at the top level (a full v0.8
    /// contract) or nested under an `"engine"` key (a modular contract with an
    /// engine side-car). "Carries engine payloads" = any of entities, templates,
    /// rules, objectives non-empty; steps alone stay on the modular path (the
    /// StepDirector already runs them — double-driving would double-narrate).
    static func detectEnginePayload(in raw: Data) -> SpatailEngineCore.ExperienceContract? {
        guard let obj = try? JSONSerialization.jsonObject(with: raw) as? [String: Any] else {
            return nil
        }
        // side-car first: {"…modular…", "engine": {v0.8 contract}}
        if let sub = obj["engine"] as? [String: Any],
           let data = try? JSONSerialization.data(withJSONObject: sub),
           let c = try? SpatailEngineCore.ExperienceContract.decode(data),
           carriesEngineWork(c) || hasEngineKeys(sub) {
            return c
        }
        guard hasEngineKeys(obj) else { return nil }
        guard let c = try? SpatailEngineCore.ExperienceContract.decode(raw),
              carriesEngineWork(c) else { return nil }
        return c
    }

    private static func hasEngineKeys(_ obj: [String: Any]) -> Bool {
        func nonEmptyArray(_ key: String) -> Bool {
            (obj[key] as? [Any]).map { !$0.isEmpty } ?? false
        }
        return nonEmptyArray("entities") || nonEmptyArray("templates")
            || nonEmptyArray("rules") || nonEmptyArray("objectives")
    }

    private static func carriesEngineWork(_ c: SpatailEngineCore.ExperienceContract) -> Bool {
        !c.entities.isEmpty || !c.templates.isEmpty
            || !c.rules.isEmpty || !c.objectives.isEmpty
    }

    // MARK: lifecycle

    init(contract: SpatailEngineCore.ExperienceContract,
         narrator: SpeechNarrator,
         playCue: @escaping (String) -> Void) {
        self.contract = contract
        self.genre = contract.kit.isEmpty ? contract.genre : contract.kit
        self.backend = RealityKitBackend()
        self.engine = SpatailEngine(backend: backend)

        backend.resolveAssetURL = { BrainClient.cachedAssetURL(for: $0) }
        backend.onSpeak = { narrator.speak($0) }
        backend.onSound = { playCue($0) }
        backend.onAnchor = { _ in /* root placement owned by ExperienceRuntime */ }
        backend.onHUD = { [weak self] fields in
            guard let self else { return }
            let ordered = fields
                .compactMap { k, v in v.asString.map { (key: k, value: $0) } }
                .sorted { $0.key < $1.key }
            self.onHUD?(HUDState(genre: self.genre, fields: ordered))
        }
        backend.emit = { [weak self] event in self?.engine.submit(event) }
    }

    /// Parent the engine's scene under `parent` (the runtime's solved placement
    /// root), prefetch assets off-main, then load the contract. `resolver` is
    /// the runtime's async asset seam (BrainClient.downloadAsset behind it).
    func mount(under parent: RealityKit.Entity, scene: RealityKit.Scene,
               resolver: ((String) async throws -> URL)?) {
        parent.addChild(backend.root)
        collisionSub = backend.attachCollisions(to: scene)

        let paths = contract.assets.map(\.usdzUrl).filter { !$0.isEmpty }
        guard let resolver, !paths.isEmpty else {
            engine.load(contract)
            loaded = true
            return
        }
        Task { [weak self] in
            // Prefetch every asset (network work happens inside the resolver,
            // off this actor's critical path) so the backend's synchronous
            // cache probe hits at load time. Failures → primitive fallback.
            await withTaskGroup(of: Void.self) { group in
                for path in paths where BrainClient.cachedAssetURL(for: path) == nil {
                    group.addTask { _ = try? await resolver(path) }
                }
            }
            guard let self, !self.loaded else { return }
            self.engine.load(self.contract)
            self.loaded = true
        }
    }

    /// Per-frame drive (main; the engine is pure Swift over a handful of
    /// entities — micro work, spec-law compliant).
    func tick(dt: Double) {
        guard loaded else { return }
        engine.tick(dt)
        publishBlackboardHUD()
    }

    /// The canonical blackboard keys (score · objectives · outcome) become the
    /// HUD even when no contract-authored `hud` rule exists — published only on
    /// change, so the per-frame cost is four dictionary reads.
    private var lastHUDSignature = ""
    private func publishBlackboardHUD() {
        var fields: [(key: String, value: String)] = []
        let bb = engine.world
        if let score = bb.get(BB.score)?.asDouble {
            fields.append((key: "score", value: String(Int(score))))
        }
        if let total = bb.get(BB.objectivesTotal)?.asDouble, total > 0 {
            let done = bb.get(BB.objectivesComplete)?.asDouble ?? 0
            fields.append((key: "goals", value: "\(Int(done))/\(Int(total))"))
        }
        if let outcome = bb.get(BB.outcome)?.asString, !outcome.isEmpty {
            fields.append((key: "outcome", value: outcome))
        }
        let signature = fields.map { "\($0.key)=\($0.value)" }.joined(separator: "|")
        guard signature != lastHUDSignature else { return }
        lastHUDSignature = signature
        onHUD?(HUDState(genre: genre, fields: fields))
    }

    // MARK: host input

    /// Route a tap. Returns true when the engine consumed it: either the hit
    /// entity lives under the engine root, or the tap hit empty space and the
    /// engine (not the modular director) owns scene taps.
    func handleTap(entity: RealityKit.Entity?, ownsSceneTaps: Bool) -> Bool {
        guard loaded else { return false }
        if let entity, isUnderRoot(entity) {
            backend.reportTap(on: entity)
            return true
        }
        if entity == nil, ownsSceneTaps {
            backend.reportTap(on: nil)
            return true
        }
        return false
    }

    /// Shooter fire control: spawn-and-shove down the camera ray. The kit's
    /// fire rule reads origin/impulse from the event payload.
    func fire(origin: SIMD3<Float>, direction: SIMD3<Float>, speed: Float = 5) {
        guard loaded else { return }
        // world → engine-root frame (the kit's spawn positions are root-local)
        let localOrigin = backend.root.convert(position: origin, from: nil)
        let dirWorld = simd_normalize(direction) * speed
        let rootRot = backend.root.orientation(relativeTo: nil).inverse
        let localImpulse = rootRot.act(dirWorld)
        engine.submit(EngineEvent(Ev.fire, payload: [
            "origin": .list([.number(Double(localOrigin.x)),
                             .number(Double(localOrigin.y)),
                             .number(Double(localOrigin.z))]),
            "impulse": .list([.number(Double(localImpulse.x)),
                              .number(Double(localImpulse.y)),
                              .number(Double(localImpulse.z))]),
        ]))
    }

    func clear() {
        collisionSub?.cancel()
        collisionSub = nil
        backend.root.removeFromParent()
        loaded = false
    }

    private func isUnderRoot(_ e: RealityKit.Entity) -> Bool {
        var cur: RealityKit.Entity? = e
        while let c = cur {
            if c === backend.root { return true }
            cur = c.parent
        }
        return false
    }
}
