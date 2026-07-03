// ExperienceRuntime.swift — the scene half of the product loop: contracts in,
// RealityKit content out, placement honesty back (LIVE_BRAIN_SPEC §2.2).
//
// Two inbound streams, one scene discipline:
//   • apply(contract:) — a /modular ask (or a Library re-place). The on-device
//     Placement Design System solves it against the room as scanned NOW:
//     PlacementSolver (arc/pad math) → PlacementPlanner (world pin + corrections)
//     → diff-applied entities. Object-anchored contracts (anchoring.mode=object)
//     mount on the registry object's landing pad (solver `.object` target) and
//     PIN that object against registry expiry.
//   • apply(delta:) — the live fusion brain's experience.delta. The brain solved
//     against the room WE uplinked, so element positions are already this
//     session's world coordinates: the runtime materializes them verbatim,
//     element-id-diffed. AppModel gates these on hasSentRoomThisConnection
//     (spec §1.5) — by the time a delta reaches here, the gate has passed.
//
// Scene law (spec §0 law 2): apply is DIFF-ONLY by element id. Existing entities
// are moved/updated in place; only new ids build; only absent ids are removed.
// Never removeAll()+rebuild — `clear()` exists solely for the user's explicit
// "clear experience" action.
//
// Assets: primitives materialize instantly (the placeholder-then-swap contract);
// USDZ models stream in through `assetResolver` (AppModel wires it to
// BrainClient.downloadAsset — the runtime owns zero networking) and swap into the
// SAME container entity, so placement never jumps. GLB stays the library format
// of record on the PC; RealityKit loads the USDZ twin.
//
// After every modular apply, a PlacementReportWire goes out through
// `onPlacementReport` — solver inputs, the plan verbatim (reasons included), and
// the final world transforms with corrections — the Client column of /traces/view.
// Step-driven changes that MOVE content re-report with a "step N reposition"
// correction (same solve context, fresh finals).
//
// On top of the placed scene sits the game-manager layer (built per apply from
// the same contract): StepDirector walks `sequence` (per-step named clips,
// focus/isolate, part effects, narration on the glass StepPanelView), TriggerVM
// runs `triggers` (tap/stepEnter/timer/approach/gaze/quizCorrect), and
// EngineHost instantiates SpatailEngine kits when the raw payload carries the
// v0.8 engine shape (rules/objectives/entities). All of it obeys the same
// diff-only law: every touch is stashed and restored, never rebuilt.

import Foundation
import ARKit
import RealityKit
import Combine
import UIKit
import simd

@MainActor
final class ExperienceRuntime: NSObject, ObservableObject {

    // MARK: Seams (wired by AppModel — the composition root)

    /// Placement honesty: fired after every applied plan (spec §2.2).
    var onPlacementReport: ((PlacementReportWire) -> Void)?
    /// Server asset path → local file URL (BrainClient.downloadAsset behind it).
    var assetResolver: ((String) async throws -> URL)?
    /// Registry objects placed experiences anchor to — exempt from expiry (spec §3).
    var onPinnedObjectsChanged: ((Set<UUID>) -> Void)?

    // MARK: Observable state (Library/HUD read these)

    @Published private(set) var currentExperienceId: String?
    @Published private(set) var currentTitle: String?
    @Published private(set) var lastAppliedDeltaVersion: Int = 0
    /// The active sequence step for the glass panel (nil = no sequenced experience).
    @Published private(set) var stepHUD: StepHUD?
    /// SpatailEngine HUD (score/objective/outcome; nil = no engine experience).
    @Published private(set) var engineHUD: EngineHost.HUDState?

    // MARK: Game-manager layer (steps · triggers · engine)
    //
    // Built per modular apply from the SAME contract that placed the scene.
    // The director/VM only touch the entities a step or trigger names — every
    // touch is stashed and restored (diff discipline holds through sequencing).

    private var director: StepDirector?
    private var triggerVM: TriggerVM?
    private var engineHost: EngineHost?
    private let narrator = SpeechNarrator()
    private var hasStepSequence = false
    private var tapRecognizer: UITapGestureRecognizer?
    private var updateSub: Cancellable?
    private weak var attachedView: ARView?
    private var lastTickAt: TimeInterval = 0
    /// Everything needed to RE-emit the placement report after a step-driven
    /// content move ("step 3 reposition" corrections — spec §2.2 honesty).
    private var reportContext: ModularReportContext?

    // MARK: Scene graph
    //
    // One world anchor at the session origin, added once. Under it:
    //   modularRoot — the solved /modular experience (transform = planner root)
    //   deltaRoot   — live-brain elements, world coordinates verbatim
    // Element containers are keyed by id in their own maps; the container never
    // changes identity across updates, so model swaps and moves are in-place.

    private var worldAnchor: AnchorEntity?
    private let modularRoot = Entity()
    private let deltaRoot = Entity()
    private var modularNodes: [String: Entity] = [:]
    private var deltaNodes: [String: Entity] = [:]
    /// Element change signatures (id → signature) so delta updates that only
    /// move an element never rebuild its content.
    private var deltaSignatures: [String: String] = [:]
    /// Pins from the two inbound streams, unioned into the registry exemption.
    private var modularPinned: Set<UUID> = []
    private var deltaPinned: Set<UUID> = []
    private var pinnedObjectIds: Set<UUID> = [] {
        didSet { if pinnedObjectIds != oldValue { onPinnedObjectsChanged?(pinnedObjectIds) } }
    }
    private func updatePinned() { pinnedObjectIds = modularPinned.union(deltaPinned) }
    /// Bumped per modular apply — a stale async model load must not swap in.
    private var modelGeneration = 0

    // MARK: - Scene attachment

    private func ensureAttached(to arView: ARView) {
        installInteraction(on: arView)
        if let anchor = worldAnchor, anchor.scene != nil { return }
        let anchor = AnchorEntity(world: SIMD3<Float>(0, 0, 0))
        anchor.addChild(modularRoot)
        anchor.addChild(deltaRoot)
        arView.scene.addAnchor(anchor)
        worldAnchor = anchor
    }

    /// One tap recognizer + one per-frame subscription per view — present() runs
    /// per experience, and stacked identical recognizers all fire (the diagnosed
    /// legacy bug: every tap advanced one extra step per re-present).
    private func installInteraction(on arView: ARView) {
        attachedView = arView
        if tapRecognizer == nil {
            let tap = UITapGestureRecognizer(target: self, action: #selector(handleTap(_:)))
            arView.addGestureRecognizer(tap)
            tapRecognizer = tap
        }
        if updateSub == nil {
            updateSub = arView.scene.subscribe(to: SceneEvents.Update.self) { [weak self] _ in
                self?.tick()
            }
        }
    }

    // MARK: - Modular contract (ask / Library re-place)

    /// `raw` = the server's verbatim response bytes. Optional-but-wanted: the
    /// engine wiring decodes v0.8 payloads (rules/objectives/entities) out of it
    /// tolerantly, so a wire contract without them behaves exactly as before.
    func apply(contract: ModularContract, raw: Data? = nil, arView: ARView,
               surfaces: [RoomSurface], objects: [SpatailObject]) {
        ensureAttached(to: arView)
        modelGeneration += 1
        let generation = modelGeneration

        // Tear the previous game layer down BEFORE the diff-apply: the director
        // restores every stashed material/opacity/overlay touch first, so nodes
        // that survive the diff carry their authored look into the new sequence.
        teardownGameLayer()

        currentExperienceId = contract.experienceId
        currentTitle = contract.title

        // ── solver inputs ────────────────────────────────────────────────────
        let ordered = orderedAssets(contract)
        let reqs: [(req: PlacementSolver.AssetReq, asset: ModularContract.Asset,
                    source: String)] = ordered.map { asset in
            let (footprint, source) = Self.footprint(for: asset)
            return (PlacementSolver.AssetReq(id: asset.id, footprint: footprint,
                                             role: asset.role,
                                             realScaleBaked: asset.realScaleBaked),
                    asset, source)
        }
        let primary = ordered.first?.id
        let anchorPreference = contract.placement.anchorType.isEmpty
            ? contract.stage.anchor : contract.placement.anchorType
        let scaleMode = Self.solverScaleMode(contract)
        let coverage = Float(contract.placement.maxSurfaceCoverage)

        // ── target: registry object (tracked stream, degraded to placed) or room ──
        var pinned: Set<UUID> = []
        let layout: PlannedLayout?
        if contract.anchoring.mode == "object",
           let target = Self.anchorObject(for: contract.anchoring, in: objects) {
            pinned.insert(target.id)
            layout = PlacementPlanner.planOnObject(target: .object(target),
                                                   assets: reqs.map(\.req),
                                                   primary: primary,
                                                   coverage: coverage)
        } else {
            let camera = arView.session.currentFrame?.camera
            let pos = camera.map {
                SIMD3<Float>($0.transform.columns.3.x, $0.transform.columns.3.y,
                             $0.transform.columns.3.z)
            } ?? SIMD3<Float>(0, 0, 0)
            let fwd = camera.map {
                SIMD3<Float>(-$0.transform.columns.2.x, -$0.transform.columns.2.y,
                             -$0.transform.columns.2.z)
            } ?? SIMD3<Float>(0, 0, -1)
            layout = PlacementPlanner.planInRoom(surfaces: surfaces, objects: objects,
                                                 userPosition: pos, userForward: fwd,
                                                 assets: reqs.map(\.req),
                                                 anchorPreference: anchorPreference,
                                                 scaleMode: scaleMode,
                                                 primary: primary,
                                                 coverage: coverage)
        }
        modularPinned = pinned
        updatePinned()

        // The room can't answer yet → raycast fallback (always place SOMETHING).
        let resolved = layout ?? Self.raycastFallback(arView: arView,
                                                      assets: reqs.map(\.req),
                                                      primary: primary,
                                                      coverage: coverage)

        // ── diff-apply by asset id ───────────────────────────────────────────
        modularRoot.transform = Transform(matrix: resolved.root)
        var seen = Set<String>()
        for entry in reqs {
            seen.insert(entry.asset.id)
            let local = resolved.locals[entry.asset.id] ?? .zero
            let node: Entity
            if let existing = modularNodes[entry.asset.id] {
                node = existing
            } else {
                node = Entity()
                node.name = "spatail.asset.\(entry.asset.id)"
                modularRoot.addChild(node)
                modularNodes[entry.asset.id] = node
            }
            node.position = local
            populate(node: node, with: entry.asset,
                     fitScale: resolved.scale, generation: generation)
        }
        for (id, node) in modularNodes where !seen.contains(id) {
            node.removeFromParent()
            modularNodes[id] = nil
        }

        // ── placement honesty (spec §2.2) ────────────────────────────────────
        report(contract: contract, layout: resolved, reqs: reqs,
               anchorPreference: anchorPreference, scaleMode: scaleMode,
               coverage: coverage, surfaces: surfaces)

        // ── game manager: step sequence, trigger VM, engine kits ────────────
        buildGameLayer(contract: contract, raw: raw, arView: arView)
    }

    // MARK: - Game layer (sequence · triggers · SpatailEngine)

    private func teardownGameLayer() {
        director?.teardown()
        director = nil
        triggerVM = nil
        engineHost?.clear()
        engineHost = nil
        stepHUD = nil
        engineHUD = nil
        hasStepSequence = false
        narrator.stop()
    }

    private func buildGameLayer(contract: ModularContract, raw: Data?, arView: ARView) {
        hasStepSequence = !contract.sequence.isEmpty

        // Step director: built whenever steps OR triggers exist — trigger
        // actions (labels/effects/clips) go through its transient, restorable
        // touch discipline even when there is no sequence to walk.
        if !contract.sequence.isEmpty || !contract.triggers.isEmpty {
            let d = StepDirector(
                root: modularRoot,
                node: { [weak self] id in self?.modularNodes[id] },
                assets: orderedAssets(contract),
                steps: contract.sequence,
                policy: contract.placement,
                experienceTitle: contract.title,
                narrator: narrator,
                onHUD: { [weak self] hud in self?.stepHUD = hud })
            d.onContentMoved = { [weak self] label in self?.reportStepMutation(label) }
            director = d
        }

        // Trigger VM: the contract's event → action bindings, genre-agnostic.
        if !contract.triggers.isEmpty {
            let vm = TriggerVM(triggers: contract.triggers, actuator: .init(
                advance: { [weak self] in self?.director?.next() },
                playClip: { [weak self] target, clip in
                    self?.director?.playClipForTrigger(target: target, clip: clip)
                },
                showLabel: { [weak self] target in
                    self?.director?.addTriggerLabel(target: target)
                },
                playSound: { [weak self] cue in self?.director?.playCue(cue) },
                applyEffect: { [weak self] target, effect in
                    self?.director?.applyTriggerEffect(target: target, effect: effect)
                }))
            triggerVM = vm
            director?.onStepEnter = { [weak vm] index, step in
                vm?.stepEntered(index: index, step: step)
            }
        }
        director?.start()

        // SpatailEngine: v0.8 payloads (rules/objectives/entities/kits) decoded
        // tolerantly from the same raw bytes — ExplainerKit/ShooterKit run under
        // the SAME solved placement root. No payload → nil → zero change.
        if let raw, let engineContract = EngineHost.detectEnginePayload(in: raw) {
            let host = EngineHost(contract: engineContract, narrator: narrator,
                                  playCue: { StepDirector.cue($0) })
            host.onHUD = { [weak self] hud in self?.engineHUD = hud }
            engineHUD = EngineHost.HUDState(genre: host.genre, fields: [])
            host.mount(under: modularRoot, scene: arView.scene, resolver: assetResolver)
            engineHost = host
            print("SPATAIL engine: kit=\(host.genre) mounted "
                  + "(\(engineContract.entities.count) entities, "
                  + "\(engineContract.rules.count) rules, "
                  + "\(engineContract.objectives.count) objectives)")
        }
    }

    // MARK: Interaction (taps route: engine → triggers → step advance)

    @objc private func handleTap(_ gesture: UITapGestureRecognizer) {
        guard let arView = attachedView else { return }
        let point = gesture.location(in: arView)
        let hit = arView.entity(at: point)

        // Engine first: hits under the engine root always belong to it; empty-
        // space taps go to the engine only when no modular sequence owns them
        // (the engine's own StepSequencerSystem advances on scene taps).
        if let host = engineHost,
           host.handleTap(entity: hit, ownsSceneTaps: !(director?.hasSteps ?? false)) {
            return
        }

        // Trigger VM: tap-on-element (part name first — most specific — then the
        // owning asset id, then scene). A consumed tap never also advances.
        var targets: [String] = []
        if let hit {
            if let part = Self.partName(for: hit) { targets.append(part) }
            if let assetId = assetId(for: hit), !targets.contains(assetId) {
                targets.append(assetId)
            }
        }
        targets.append("scene")
        if let vm = triggerVM, vm.fire(event: "tap", targets: targets) { return }

        director?.advanceViaTap()
    }

    /// Walk up from a hit entity to its owning modular container's asset id.
    private func assetId(for entity: Entity) -> String? {
        var cursor: Entity? = entity
        while let c = cursor {
            if let id = modularNodes.first(where: { $0.value === c })?.key { return id }
            cursor = c.parent
        }
        return nil
    }

    private static func partName(for entity: Entity) -> String? {
        let n = entity.name
        guard !n.isEmpty, n != "placeholder", n != "model",
              !n.hasPrefix("spatail.") else { return nil }
        return n
    }

    // MARK: Per-frame tick (marker pulse · spatial/timer triggers · engine)

    private func tick() {
        guard director != nil || triggerVM != nil || engineHost != nil else { return }
        let now = CACurrentMediaTime()
        let dt = lastTickAt == 0 ? 1.0 / 60.0 : min(max(now - lastTickAt, 0), 0.1)
        lastTickAt = now

        director?.tick(now: now)

        if let vm = triggerVM, !vm.isEmpty {
            var camera: (position: SIMD3<Float>, forward: SIMD3<Float>)?
            if let view = attachedView {
                let t = view.cameraTransform
                let f = -t.matrix.columns.2
                camera = (t.translation, SIMD3(f.x, f.y, f.z))
            }
            vm.tick(now: now, camera: camera,
                    elementIds: Array(modularNodes.keys),
                    elementWorld: { [weak self] id in
                        self?.modularNodes[id]?.position(relativeTo: nil)
                    })
        }

        engineHost?.tick(dt: dt)
    }

    // MARK: Step/quiz surface for the glass panel (StepPanelView)

    func stepNext() { director?.next() }
    func stepPrevious() { director?.previous() }

    /// Quiz option answered on the glass panel. Correct → onQuizCorrect triggers
    /// (advance falls through when no trigger claims it).
    func quizAnswered(correct: Bool) {
        director?.quizAnswered(correct: correct)
        guard correct else { return }
        var targets: [String] = []
        if let stepId = director?.currentStep?.id, !stepId.isEmpty { targets.append(stepId) }
        targets.append("scene")
        let consumed = triggerVM?.fire(event: "quizCorrect", targets: targets) ?? false
        if !consumed {
            // brief beat so the correct-cue lands before the scene moves on
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 600_000_000)
                self?.director?.next()
            }
        }
    }

    /// Shooter fire control (engine kits): projectile down the camera ray.
    func engineFire() {
        guard let host = engineHost, let view = attachedView else { return }
        let t = view.cameraTransform
        let f = -t.matrix.columns.2
        host.fire(origin: t.translation, direction: SIMD3(f.x, f.y, f.z))
    }

    /// Hero first (the solver's arc puts index 0 dead ahead).
    private func orderedAssets(_ contract: ModularContract) -> [ModularContract.Asset] {
        contract.assets.sorted {
            (Self.isHero($0) ? 0 : 1) < (Self.isHero($1) ? 0 : 1)
        }
    }

    private static func isHero(_ asset: ModularContract.Asset) -> Bool {
        asset.role == "hero" || asset.role == "primary" || asset.role == "subject"
    }

    /// Footprint + provenance (spec §2.3): metric contract → library; the
    /// director's LLM guess → object_size_llm; neither → default guess.
    private static func footprint(for asset: ModularContract.Asset)
        -> (SIMD3<Float>, String) {
        if let real = asset.realSizeMeters, real.count >= 3 {
            return (SIMD3(Float(real[0]), Float(real[1]), Float(real[2])), "library")
        }
        if asset.realScaleBaked {
            let s = asset.scaleMeters
            let v = s.count >= 3 ? SIMD3(Float(s[0]), Float(s[1]), Float(s[2]))
                                 : SIMD3<Float>(0.2, 0.2, 0.2)
            return (v, "library")
        }
        let s = asset.scaleMeters
        if s.count >= 3, s.contains(where: { $0 != 0.2 }) {
            return (SIMD3(Float(s[0]), Float(s[1]), Float(s[2])), "object_size_llm")
        }
        return (SIMD3(0.2, 0.2, 0.2), "default_guess")
    }

    private static func solverScaleMode(_ contract: ModularContract) -> String {
        switch contract.placement.scaleMode {
        case "true_scale": return "real"
        case "": return contract.stage.scaleMode
        default: return "dynamic"
        }
    }

    /// Resolve anchoring.objectId against the registry: UUID match first, then
    /// label (the brain sometimes echoes the noun rather than our UUID).
    private static func anchorObject(for anchoring: ModularContract.Anchoring,
                                     in objects: [SpatailObject]) -> SpatailObject? {
        if let uuid = UUID(uuidString: anchoring.objectId),
           let hit = objects.first(where: { $0.id == uuid }) {
            return hit
        }
        let needle = anchoring.objectId.lowercased()
        guard !needle.isEmpty else { return nil }
        return objects.first {
            $0.label.map { needle.contains($0.lowercased()) || $0.lowercased().contains(needle) } ?? false
        }
    }

    /// No usable surface yet: raycast the screen center onto whatever geometry
    /// ARKit has (estimated planes last), else 1.2 m ahead of the camera.
    private static func raycastFallback(arView: ARView,
                                        assets: [PlacementSolver.AssetReq],
                                        primary: String?,
                                        coverage: Float) -> PlannedLayout {
        let center = CGPoint(x: arView.bounds.midX, y: arView.bounds.midY)
        let camera = arView.session.currentFrame?.camera
        var anchorPoint: SIMD3<Float>
        if let hit = (arView.raycast(from: center, allowing: .existingPlaneGeometry,
                                     alignment: .any).first
                      ?? arView.raycast(from: center, allowing: .estimatedPlane,
                                        alignment: .any).first) {
            let c = hit.worldTransform.columns.3
            anchorPoint = SIMD3(c.x, c.y, c.z)
        } else if let t = camera?.transform {
            let pos = SIMD3<Float>(t.columns.3.x, t.columns.3.y, t.columns.3.z)
            let fwd = SIMD3<Float>(-t.columns.2.x, -t.columns.2.y, -t.columns.2.z)
            anchorPoint = pos + simd_normalize(SIMD3(fwd.x, 0, fwd.z)) * 1.2
            anchorPoint.y = pos.y - 1.0
        } else {
            anchorPoint = SIMD3(0, -1, -1.2)
        }

        // Face the user, arc the assets around the anchor point.
        var yaw: Float = 0
        if let t = camera?.transform {
            let user = SIMD3<Float>(t.columns.3.x, t.columns.3.y, t.columns.3.z)
            let to = user - anchorPoint
            yaw = atan2(to.x, to.z)
        }
        let rot = simd_quatf(angle: yaw, axis: SIMD3(0, 1, 0))
        var root = simd_float4x4(rot)
        root.columns.3 = SIMD4(anchorPoint.x, anchorPoint.y, anchorPoint.z, 1)

        var locals: [String: SIMD3<Float>] = [:]
        var placements: [PlacementSolver.Placement] = []
        for (i, a) in assets.enumerated() {
            let offset = SIMD3<Float>(Float(i) * 0.35 - Float(assets.count - 1) * 0.175,
                                      0, 0)
            locals[a.id] = offset
            placements.append(PlacementSolver.Placement(
                assetId: a.id, position: anchorPoint + rot.act(offset), yaw: yaw,
                scale: 1.0, anchor: "raycast", zone: i == 0 ? "hero" : "secondary",
                fits: true,
                reason: "raycast fallback — no measured surface answered yet"))
        }
        let plan = PlacementSolver.Plan(
            placements: placements, anchor: "raycast", scaleVariant: "tabletop",
            diagnostics: PlacementSolver.Diagnostics(assetCount: assets.count,
                                                     source: "raycast-fallback"))
        return PlannedLayout(root: root, locals: locals, scale: 1.0, plan: plan,
                             corrections: ["raycast-fallback (no surface)"], room: nil)
    }

    // MARK: Asset materialization (placeholder now, real model swap later)

    private func populate(node: Entity, with asset: ModularContract.Asset,
                          fitScale: Float, generation: Int) {
        // Placeholder primitive immediately (idempotent: reuse if present).
        if node.findEntity(named: "placeholder") == nil,
           node.findEntity(named: "model") == nil {
            let placeholder = Self.primitive(for: asset, fitScale: fitScale)
            placeholder.name = "placeholder"
            placeholder.generateCollisionShapes(recursive: false)   // tappable now
            node.addChild(placeholder)
        }

        // Real model: USDZ (RealityKit-native twin of the GLB of record).
        let path = asset.usdzUrl
        guard !path.isEmpty, let resolver = assetResolver,
              node.findEntity(named: "model") == nil else { return }
        Task { [weak self] in
            guard let url = try? await resolver(path) else { return }
            guard let self, self.modelGeneration == generation else { return }
            guard let loaded = try? await Self.loadModel(from: url) else { return }
            guard self.modelGeneration == generation,
                  node.findEntity(named: "model") == nil else { return }
            loaded.name = "model"
            Self.fit(loaded, asset: asset, fitScale: fitScale)
            node.findEntity(named: "placeholder")?.removeFromParent()
            node.addChild(loaded)
            // Tap-on-element/part triggers need collision on the real geometry.
            loaded.generateCollisionShapes(recursive: true)
            // Story region overlays ship visible in the USDZ but stay OFF until
            // a step summons them.
            StepDirector.disableRegionOverlays(in: loaded)
            if let director = self.director, director.hasSteps {
                // The sequence owns playback: re-anchor the current step's
                // visuals and start ITS named clip (not auto-play-all).
                director.modelDidSwap(assetId: asset.id)
            } else if asset.supportsAnimation {
                // No sequence → the legacy default: play everything, looped.
                Self.playClips(loaded)
            }
        }
    }

    private static func primitive(for asset: ModularContract.Asset,
                                  fitScale: Float) -> ModelEntity {
        let s = asset.scaleMeters
        let size = SIMD3<Float>(s.count > 0 ? Float(s[0]) : 0.2,
                                s.count > 1 ? Float(s[1]) : 0.2,
                                s.count > 2 ? Float(s[2]) : 0.2)
            * (asset.realScaleBaked ? 1.0 : fitScale)
        let mesh: MeshResource
        switch asset.fallbackPrimitive {
        case "sphere": mesh = .generateSphere(radius: max(size.x, max(size.y, size.z)) / 2)
        case "plane":  mesh = .generatePlane(width: size.x, depth: size.z)
        default:       mesh = .generateBox(size: size, cornerRadius: min(size.x, size.y) * 0.1)
        }
        var material = PhysicallyBasedMaterial()
        material.baseColor = .init(tint: UIColor(SpatailColor.indigo300).withAlphaComponent(0.85))
        material.roughness = 0.6
        let entity = ModelEntity(mesh: mesh, materials: [material])
        entity.position.y = size.y / 2      // rest ON the surface, not through it
        return entity
    }

    /// Real-scale contract (asset_service): baked → 1.0; realSizeMeters → fit the
    /// longest dimension; else the solver's uniform fit scale on the footprint.
    private static func fit(_ entity: Entity, asset: ModularContract.Asset,
                            fitScale: Float) {
        if asset.realScaleBaked {
            entity.scale = .one
        } else {
            let bounds = entity.visualBounds(relativeTo: nil)
            let maxDim = max(bounds.extents.x, max(bounds.extents.y, bounds.extents.z))
            if maxDim > 0 {
                let target: Float
                if let real = asset.realSizeMeters, real.count >= 3 {
                    target = Float(max(real[0], max(real[1], real[2])))
                } else {
                    let s = asset.scaleMeters
                    let footprint = s.count >= 3
                        ? Float(max(s[0], max(s[1], s[2]))) : 0.2
                    target = footprint * fitScale
                }
                entity.scale = SIMD3(repeating: target / maxDim)
            }
        }
        // Seat the model's bottom on the node origin (the solved slot).
        let seated = entity.visualBounds(relativeTo: nil)
        entity.position.y -= seated.min.y
    }

    /// Async USDZ load with an iOS 17 path (the async `Entity(contentsOf:)`
    /// initializer only exists on iOS 18; below that, bridge the Combine
    /// LoadRequest — still fully off-main, never the blocking `Entity.load`).
    private static func loadModel(from url: URL) async throws -> Entity {
        if #available(iOS 18.0, *) {
            return try await Entity(contentsOf: url)
        }
        var iterator = Entity.loadAsync(contentsOf: url).values.makeAsyncIterator()
        guard let entity = try await iterator.next() else {
            throw URLError(.cannotDecodeContentData)
        }
        return entity
    }

    private static func playClips(_ entity: Entity) {
        func play(_ e: Entity) {
            for animation in e.availableAnimations {
                e.playAnimation(animation.repeat(), transitionDuration: 0.25,
                                startsPaused: false)
            }
            e.children.forEach(play)
        }
        play(entity)
    }

    // MARK: - experience.delta (live fusion brain, world coordinates verbatim)

    func apply(delta: ExperienceDeltaWire, arView: ARView,
               surfaces: [RoomSurface], objects: [SpatailObject]) {
        guard let experience = delta.experience else { return }
        guard delta.version != lastAppliedDeltaVersion else { return }   // dedup
        ensureAttached(to: arView)
        lastAppliedDeltaVersion = delta.version
        currentExperienceId = experience.experienceId
        currentTitle = experience.title

        // ── part-addressed target (delta.target {objectId, part}) ───────────
        // The brain asked for the plan to land ON a registry object (or a named
        // part of it). Route through the SAME PlacementTarget .object/.part
        // solve path the modular stream uses: the solver's landing region (the
        // part's resolved sub-OBB, the §3 cap/lid/top fallback slice, or the
        // parent's top face) re-bases the elements; the brain's own layout is
        // still what the report's `plan` carries (intent vs reality).
        var overrides: [String: (position: SIMD3<Float>, yaw: Float)] = [:]
        var targetCorrections: [String] = []
        deltaPinned = []
        if let ref = delta.target {
            if let obj = Self.resolveTargetObject(ref.objectId, in: objects) {
                let part: SpatailPart? = ref.part.map { label in
                    obj.parts.first { $0.label.lowercased() == label.lowercased() }
                        ?? obj.parts.first { $0.label.lowercased().contains(label.lowercased()) }
                        // unknown part label → synthesize; the solver's §3
                        // fallback (top-slice for cap/lid/top) still applies
                        ?? SpatailPart(label: label, box: nil, region: nil, confidence: 0)
                }
                let target: PlacementTarget = part.map { .part(obj, $0) } ?? .object(obj)
                let reqs = experience.spatialElements.map { el -> PlacementSolver.AssetReq in
                    let (w, h, d) = el.placement.boxSizeMeters
                    return PlacementSolver.AssetReq(id: el.id, footprint: SIMD3(w, h, d),
                                                    role: el.contentType)
                }
                if let layout = PlacementPlanner.planOnObject(
                        target: target, assets: reqs,
                        primary: experience.spatialElements.first?.id,
                        coverage: 0.8) {
                    let rootT = Transform(matrix: layout.root)
                    for (id, local) in layout.locals {
                        overrides[id] = (rootT.rotation.act(local) + rootT.translation,
                                         obj.obb.yaw)
                    }
                    deltaPinned.insert(obj.id)   // anchored → exempt from expiry (§3)
                    let site = ([obj.label ?? "object"] + (ref.part.map { [$0] } ?? []))
                        .joined(separator: "·")
                    targetCorrections = ["target-pin \(site)"]
                }
            }
            if overrides.isEmpty {
                targetCorrections = ["target-unresolved("
                    + (ref.objectId ?? "?")
                    + (ref.part.map { "·" + $0 } ?? "") + ") — brain plan verbatim"]
            }
        }
        updatePinned()

        var seen = Set<String>()
        var finals: [PlacementReportWire.FinalPlacement] = []
        var planned: [PlacementReportWire.PlannedPlacement] = []

        for element in experience.spatialElements {
            seen.insert(element.id)
            let node: Entity
            if let existing = deltaNodes[element.id] {
                node = existing
            } else {
                node = Entity()
                node.name = "spatail.element.\(element.id)"
                deltaRoot.addChild(node)
                deltaNodes[element.id] = node
            }
            materialize(element: element, into: node, override: overrides[element.id])

            let world = node.transformMatrix(relativeTo: nil)
            finals.append(.init(elementId: element.id,
                                worldTransform: Self.flatten(world),
                                renderScale: 1.0, corrections: targetCorrections))
            let p = element.placement.simdPosition
            planned.append(.init(elementId: element.id,
                                 position: [Double(p.x), Double(p.y), Double(p.z)],
                                 yaw: Double(element.placement.simdRotation.y),
                                 scale: 1.0, fits: true,
                                 reason: element.whyThisPlacement))
        }
        for (id, node) in deltaNodes where !seen.contains(id) {
            node.removeFromParent()
            deltaNodes[id] = nil
            deltaSignatures[id] = nil
        }

        // Placement honesty for the live stream too: the brain planned it, the
        // client reports what it actually rendered (verbatim world transforms).
        let report = PlacementReportWire(
            experienceId: experience.experienceId,
            reportedAt: Date().timeIntervalSince1970,
            solverInputs: .init(
                anchorPreference: experience.environmentAssumptions.anchorObject
                    ?? experience.environmentAssumptions.kind ?? "surface",
                scaleMode: "verbatim",
                coverage: 0,
                footprints: [],
                roomSummary: .init(surfaces: surfaces.map { .init(surface: $0) })),
            plan: .init(anchor: "brain", placements: planned),
            finalPlacements: finals)
        onPlacementReport?(report)
    }

    /// Resolve delta.target.objectId against the registry: UUID match first,
    /// then label (the brain sometimes echoes the noun rather than our UUID).
    private static func resolveTargetObject(_ idOrLabel: String?,
                                            in objects: [SpatailObject]) -> SpatailObject? {
        guard let raw = idOrLabel, !raw.isEmpty else { return nil }
        if let uuid = UUID(uuidString: raw),
           let hit = objects.first(where: { $0.id == uuid }) {
            return hit
        }
        let needle = raw.lowercased()
        return objects.first {
            $0.label.map { needle.contains($0.lowercased()) || $0.lowercased().contains(needle) } ?? false
        }
    }

    /// Build/refresh one live element's content in place. The container node
    /// survives updates; only its children rebuild when the element changed.
    /// `override` = part-addressed landing (world position + parent yaw) from
    /// the .object/.part solve; nil → the brain's coordinates verbatim.
    private func materialize(element: SpatialElement, into node: Entity,
                             override: (position: SIMD3<Float>, yaw: Float)? = nil) {
        // Edge strips do their own world-frame layout math — never re-based.
        let isEdge = element.placement.kind == "surface_edge"
        let position = (isEdge ? nil : override?.position) ?? element.placement.simdPosition

        let signature = Self.signature(for: element)
        if deltaSignatures[element.id] == signature {
            // Geometry unchanged → position-only update (cheap, in place).
            node.position = position
            if let override, !isEdge {
                node.orientation = simd_quatf(angle: override.yaw, axis: SIMD3(0, 1, 0))
            }
            return
        }
        deltaSignatures[element.id] = signature
        node.children.forEach { $0.removeFromParent() }   // this element only
        node.position = position

        if isEdge {
            // Edge units are laid out in world coordinates relative to the
            // node's position — keep the node unrotated so that math holds.
            node.orientation = simd_quatf(angle: 0, axis: SIMD3(0, 1, 0))
            buildEdgeStrip(element: element, into: node)
        } else {
            let yaw = override?.yaw ?? element.placement.simdRotation.y
            node.orientation = simd_quatf(angle: yaw, axis: SIMD3(0, 1, 0))
            buildBody(element: element, into: node)
        }

        if let title = displayTitle(for: element) {
            node.addChild(Self.label(title))
        }
    }

    private func buildBody(element: SpatialElement, into node: Entity) {
        let (w, h, d) = element.placement.boxSizeMeters
        let isPanel = element.representationMode.contains("panel")
            || element.contentType == "text" || element.contentType == "summary"
        let mesh: MeshResource = isPanel
            ? .generateBox(size: SIMD3(w, h, 0.015), cornerRadius: 0.01)
            : .generateBox(size: SIMD3(w, h, d), cornerRadius: min(w, h) * 0.08)
        var material = PhysicallyBasedMaterial()
        material.baseColor = .init(tint: isPanel
            ? UIColor(SpatailColor.paper).withAlphaComponent(0.92)
            : UIColor(SpatailColor.indigo300).withAlphaComponent(0.85))
        material.roughness = 0.5
        let body = ModelEntity(mesh: mesh, materials: [material])
        body.position.y = h / 2
        node.addChild(body)
    }

    /// surface_edge: discrete units along the measured from→to segment (the
    /// brain recorded the geometry; the client draws it without re-solving).
    private func buildEdgeStrip(element: SpatialElement, into node: Entity) {
        guard let from = element.placement.simdFrom,
              let to = element.placement.simdTo else {
            buildBody(element: element, into: node)
            return
        }
        let count = max(element.placement.count ?? 1, 1)
        let span = simd_length(to - from)
        guard span > 0.01 else { return }
        let unitLength = span / Float(count) * 0.92
        let dir = simd_normalize(to - from)
        var material = PhysicallyBasedMaterial()
        material.baseColor = .init(tint: UIColor(SpatailColor.indigo500).withAlphaComponent(0.9))
        for i in 0..<count {
            let t = (Float(i) + 0.5) / Float(count)
            let center = from + dir * (span * t)
            let unit = ModelEntity(
                mesh: .generateBox(size: SIMD3(unitLength, 0.03, 0.05), cornerRadius: 0.008),
                materials: [material])
            unit.position = center - node.position(relativeTo: nil)
            unit.orientation = simd_quatf(from: SIMD3(1, 0, 0), to: dir)
            node.addChild(unit)
        }
    }

    private func displayTitle(for element: SpatialElement) -> String? {
        let title = element.sourceContent?.title ?? element.title
        return title.isEmpty ? nil : title
    }

    private static func label(_ text: String) -> ModelEntity {
        let mesh = MeshResource.generateText(
            String(text.prefix(48)),
            extrusionDepth: 0.002,
            font: .systemFont(ofSize: 0.05, weight: .medium),
            containerFrame: .zero, alignment: .center, lineBreakMode: .byTruncatingTail)
        let entity = ModelEntity(mesh: mesh,
                                 materials: [UnlitMaterial(color: UIColor(SpatailColor.paper))])
        // Center the text over the element, floating just above it.
        let bounds = mesh.bounds
        entity.position = SIMD3(-bounds.center.x, 0.06, 0)
        return entity
    }

    private static func signature(for element: SpatialElement) -> String {
        let p = element.placement
        let pos = p.position?.map { String(format: "%.3f", $0) }.joined(separator: ",") ?? ""
        return "\(element.contentType)|\(element.representationMode)|\(p.kind ?? "")|" +
               "\(pos)|\(p.sizeMeters?.description ?? "")|\(element.title)"
    }

    private static func flatten(_ m: simd_float4x4) -> [Float] {
        [m.columns.0.x, m.columns.0.y, m.columns.0.z, m.columns.0.w,
         m.columns.1.x, m.columns.1.y, m.columns.1.z, m.columns.1.w,
         m.columns.2.x, m.columns.2.y, m.columns.2.z, m.columns.2.w,
         m.columns.3.x, m.columns.3.y, m.columns.3.z, m.columns.3.w]
    }

    // MARK: - Placement report (modular path — spec §2.2, field-for-field)

    /// The captured solve, kept so step-driven content moves can RE-report with
    /// their correction appended ("step 3 reposition") without re-solving.
    private struct ModularReportContext {
        let experienceId: String
        let anchorPreference: String
        let scaleMode: String
        let coverage: Float
        let footprints: [PlacementReportWire.Footprint]
        let planAnchor: String
        let planned: [PlacementReportWire.PlannedPlacement]
        let baseCorrections: [String]
        let renderScale: Float
        let roomSummary: PlacementReportWire.RoomSummary
    }

    /// Re-emit the placement report after a step/trigger-driven change MOVED
    /// content: same solver inputs and plan (the intent is unchanged), fresh
    /// final world transforms, corrections gaining the step's entry.
    private func reportStepMutation(_ label: String) {
        guard let ctx = reportContext else { return }
        let finals = modularNodes.map { id, node -> PlacementReportWire.FinalPlacement in
            .init(elementId: id,
                  worldTransform: Self.flatten(node.transformMatrix(relativeTo: nil)),
                  renderScale: Double(ctx.renderScale),
                  corrections: ctx.baseCorrections + [label])
        }
        let report = PlacementReportWire(
            experienceId: ctx.experienceId,
            reportedAt: Date().timeIntervalSince1970,
            solverInputs: .init(anchorPreference: ctx.anchorPreference,
                                scaleMode: ctx.scaleMode,
                                coverage: Double(ctx.coverage),
                                footprints: ctx.footprints,
                                roomSummary: ctx.roomSummary),
            plan: .init(anchor: ctx.planAnchor, placements: ctx.planned),
            finalPlacements: finals)
        onPlacementReport?(report)
    }

    private func report(contract: ModularContract,
                        layout: PlannedLayout,
                        reqs: [(req: PlacementSolver.AssetReq,
                                asset: ModularContract.Asset, source: String)],
                        anchorPreference: String, scaleMode: String,
                        coverage: Float, surfaces: [RoomSurface]) {
        let footprints = reqs.map { entry in
            PlacementReportWire.Footprint(
                assetId: entry.req.id,
                meters: [Double(entry.req.footprint.x),
                         Double(entry.req.footprint.y),
                         Double(entry.req.footprint.z)],
                source: entry.source)
        }
        let planned = layout.plan.placements.map { p in
            PlacementReportWire.PlannedPlacement(
                elementId: p.assetId,
                position: [Double(p.position.x), Double(p.position.y), Double(p.position.z)],
                yaw: Double(p.yaw), scale: Double(p.scale), fits: p.fits,
                reason: p.reason)
        }
        let finals = layout.plan.placements.map { p -> PlacementReportWire.FinalPlacement in
            let world = modularNodes[p.assetId]?.transformMatrix(relativeTo: nil)
                ?? matrix_identity_float4x4
            return .init(elementId: p.assetId,
                         worldTransform: Self.flatten(world),
                         renderScale: Double(layout.scale),
                         corrections: layout.corrections)
        }
        let roomSummary = layout.room.map { rm in
            PlacementReportWire.RoomSummary(surfaces: rm.surfaces.map {
                .init(kind: $0.cls,
                      sizeMeters: [Double($0.size.x), Double($0.size.y)],
                      y: Double($0.height))
            })
        } ?? PlacementReportWire.RoomSummary(surfaces: surfaces.map { .init(surface: $0) })

        let report = PlacementReportWire(
            experienceId: contract.experienceId,
            reportedAt: Date().timeIntervalSince1970,
            solverInputs: .init(anchorPreference: anchorPreference,
                                scaleMode: scaleMode,
                                coverage: Double(coverage),
                                footprints: footprints,
                                roomSummary: roomSummary),
            plan: .init(anchor: layout.plan.anchor, placements: planned),
            finalPlacements: finals)
        // Keep the solve so step-driven moves can re-report honestly (§2.2).
        reportContext = ModularReportContext(
            experienceId: contract.experienceId,
            anchorPreference: anchorPreference,
            scaleMode: scaleMode,
            coverage: coverage,
            footprints: footprints,
            planAnchor: layout.plan.anchor,
            planned: planned,
            baseCorrections: layout.corrections,
            renderScale: layout.scale,
            roomSummary: roomSummary)
        onPlacementReport?(report)
    }

    // MARK: - Clear (the user's explicit action — the ONLY bulk removal)

    func clear() {
        teardownGameLayer()
        for (_, node) in modularNodes { node.removeFromParent() }
        for (_, node) in deltaNodes { node.removeFromParent() }
        modularNodes.removeAll()
        deltaNodes.removeAll()
        deltaSignatures.removeAll()
        modelGeneration += 1
        modularPinned = []
        deltaPinned = []
        updatePinned()
        reportContext = nil
        currentExperienceId = nil
        currentTitle = nil
    }
}
