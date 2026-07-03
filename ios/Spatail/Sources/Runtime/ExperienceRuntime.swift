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

import Foundation
import ARKit
import RealityKit
import UIKit
import simd

@MainActor
final class ExperienceRuntime: ObservableObject {

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
    private var pinnedObjectIds: Set<UUID> = [] {
        didSet { if pinnedObjectIds != oldValue { onPinnedObjectsChanged?(pinnedObjectIds) } }
    }
    /// Bumped per modular apply — a stale async model load must not swap in.
    private var modelGeneration = 0

    // MARK: - Scene attachment

    private func ensureAttached(to arView: ARView) {
        if let anchor = worldAnchor, anchor.scene != nil { return }
        let anchor = AnchorEntity(world: SIMD3<Float>(0, 0, 0))
        anchor.addChild(modularRoot)
        anchor.addChild(deltaRoot)
        arView.scene.addAnchor(anchor)
        worldAnchor = anchor
    }

    // MARK: - Modular contract (ask / Library re-place)

    func apply(contract: ModularContract, arView: ARView,
               surfaces: [RoomSurface], objects: [SpatailObject]) {
        ensureAttached(to: arView)
        modelGeneration += 1
        let generation = modelGeneration

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
        pinnedObjectIds = pinned

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
            if asset.supportsAnimation { Self.playClips(loaded) }
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
            materialize(element: element, into: node)

            let world = node.transformMatrix(relativeTo: nil)
            finals.append(.init(elementId: element.id,
                                worldTransform: Self.flatten(world),
                                renderScale: 1.0, corrections: []))
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

    /// Build/refresh one live element's content in place. The container node
    /// survives updates; only its children rebuild when the element changed.
    private func materialize(element: SpatialElement, into node: Entity) {
        let signature = Self.signature(for: element)
        if deltaSignatures[element.id] == signature {
            // Geometry unchanged → position-only update (cheap, in place).
            node.position = element.placement.simdPosition
            return
        }
        deltaSignatures[element.id] = signature
        node.children.forEach { $0.removeFromParent() }   // this element only
        node.position = element.placement.simdPosition

        switch element.placement.kind {
        case "surface_edge":
            // Edge units are laid out in world coordinates relative to the
            // node's position — keep the node unrotated so that math holds.
            node.orientation = simd_quatf(angle: 0, axis: SIMD3(0, 1, 0))
            buildEdgeStrip(element: element, into: node)
        default:
            let r = element.placement.simdRotation
            node.orientation = simd_quatf(angle: r.y, axis: SIMD3(0, 1, 0))
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
        onPlacementReport?(report)
    }

    // MARK: - Clear (the user's explicit action — the ONLY bulk removal)

    func clear() {
        for (_, node) in modularNodes { node.removeFromParent() }
        for (_, node) in deltaNodes { node.removeFromParent() }
        modularNodes.removeAll()
        deltaNodes.removeAll()
        deltaSignatures.removeAll()
        modelGeneration += 1
        pinnedObjectIds = []
        currentExperienceId = nil
        currentTitle = nil
    }
}
