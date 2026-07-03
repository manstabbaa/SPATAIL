// TruthOverlayView.swift — the verification instrument, one toggle away.
//
// "If you can't see what it saw, it doesn't ship" (spec §0 law 5). One switch
// shows every layer of what the system currently believes:
//   (a) surface boundaries — concave outlines exactly as the scanner published
//       them (3D, TruthOverlayRenderer) with kind + confidence labels (2D);
//   (b) object OBB wireframes with fused label + confidence + identification
//       age — "bottle · 0.91 · 1.2s";
//   (c) the current plan's binding highlighted in the accent + a matchReason
//       card whenever the experience delta carries fused info;
//   (d) the last VLM detections as 2D boxes, ASPECT-FILL CORRECT via
//       ARFrame.displayTransform (VisionBoxMapper — the misaligned-overlay fix).
//
// 3D content lives on a dedicated RealityKit anchor updated by per-id diffing;
// all 2D chrome is SwiftUI. Labels reproject on a 10 Hz frame consumer — the
// underlying geometry rides ARKit at full rate, only the text follows slower.

import SwiftUI
import ARKit
import RealityKit
import Combine

// MARK: - View

struct TruthOverlayView: View {
    @EnvironmentObject private var hub: ARSessionHub
    @EnvironmentObject private var scanner: RoomScannerService
    @EnvironmentObject private var registry: ObjectRegistry
    @EnvironmentObject private var uplink: VisionUplink
    @StateObject private var model = TruthOverlayModel()

    var body: some View {
        ZStack {
            // (d) last VLM detections — 2D boxes, aspect-fill corrected
            ForEach(model.vlmBoxes) { box in
                VLMBoxView(box: box)
            }

            // (a) surface labels
            ForEach(model.surfaceLabels) { label in
                TruthTag(text: label.text, dot: label.dot, accent: label.accent)
                    .position(label.point)
            }

            // (b) object labels — "bottle · 0.91 · 1.2s" + measured dimensions
            ForEach(model.objectLabels) { label in
                TruthTag(text: label.text, dot: label.dot, accent: label.accent,
                         sublines: label.sublines)
                    .position(label.point)
            }

            // (c) binding card + legend
            VStack(spacing: SpatailSpace.s2) {
                Spacer()
                if let binding = model.binding {
                    BindingCard(binding: binding)
                }
                legend
            }
            .padding(.horizontal, SpatailSpace.s4)
            .padding(.bottom, SpatailSpace.s10 + SpatailSpace.s5) // clear the AskBar
        }
        .allowsHitTesting(false)   // pure instrument — never eats a tap
        .onAppear {
            // Deferred: attach's @Published sinks emit synchronously with the
            // current value — mutating observed state during the update
            // transaction severs the AttributeGraph (the dead-eye bug).
            DispatchQueue.main.async {
                model.attach(hub: hub, scanner: scanner, registry: registry, uplink: uplink)
            }
        }
        .onDisappear { model.detach() }
    }

    // MARK: Legend (the color code, same palette the 3D lines use)

    private var legend: some View {
        HStack(spacing: SpatailSpace.s3) {
            ForEach(model.legendKinds, id: \.self) { kind in
                HStack(spacing: SpatailSpace.s1) {
                    Circle()
                        .fill(TruthPalette.color(for: kind))
                        .frame(width: 7, height: 7)
                    Text(kind.rawValue)
                        .spatailType(.micro)
                        .foregroundStyle(SpatailColor.paper)
                }
            }
            if !model.legendKinds.isEmpty {
                HStack(spacing: SpatailSpace.s1) {
                    Circle().fill(TruthPalette.bound).frame(width: 7, height: 7)
                    Text("bound")
                        .spatailType(.micro)
                        .foregroundStyle(SpatailColor.paper)
                }
            }
            // Curation honesty: hidden fragments are counted, never denied.
            if model.hiddenSurfaceCount > 0 {
                Text("+\(model.hiddenSurfaceCount) minor surfaces hidden")
                    .spatailType(.micro)
                    .foregroundStyle(SpatailColor.paper.opacity(0.65))
            }
        }
        .padding(.horizontal, SpatailSpace.s3)
        .padding(.vertical, SpatailSpace.s1 + 2)
        .background(Capsule().fill(SpatailColor.ink900.opacity(0.55)))
    }
}

// MARK: - Pieces

/// A tiny projected tag: color dot + monospaced-feel micro text on a dark pill.
/// `sublines` carry the Form Engine's measured-dimension truth ("⌀ 63 · h 218 ·
/// cap ⌀ 31 mm" / "measured · 62% arc") — the caliper-verification instrument.
private struct TruthTag: View {
    let text: String
    let dot: Color
    let accent: Bool
    var sublines: [String] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: SpatailSpace.s1) {
                Circle().fill(dot).frame(width: 6, height: 6)
                Text(text)
                    .spatailType(.micro, weight: .medium)
                    .foregroundStyle(SpatailColor.paper)
                    .lineLimit(1)
            }
            ForEach(sublines, id: \.self) { line in
                Text(line)
                    .spatailType(.micro)
                    .foregroundStyle(SpatailColor.paper.opacity(0.82))
                    .lineLimit(1)
                    .padding(.leading, SpatailSpace.s1 + 6)
            }
        }
        .padding(.horizontal, SpatailSpace.s2)
        .padding(.vertical, SpatailSpace.s1)
        .background(
            RoundedRectangle(cornerRadius: SpatailRadius.sm, style: .continuous)
                .fill(accent ? SpatailColor.indigo500.opacity(0.78)
                             : SpatailColor.ink900.opacity(0.55))
        )
        .fixedSize()
    }
}

/// One VLM detection box in view space (already aspect-fill corrected).
private struct VLMBoxView: View {
    let box: TruthOverlayModel.VLMBox

    var body: some View {
        RoundedRectangle(cornerRadius: SpatailRadius.xs, style: .continuous)
            .stroke(SpatailColor.indigo300, lineWidth: 2)
            .frame(width: box.rect.width, height: box.rect.height)
            .overlay(alignment: .topLeading) {
                Text("\(box.label) \(Int(box.confidence * 100))%")
                    .spatailType(.micro, weight: .medium)
                    .foregroundStyle(SpatailColor.ink900)
                    .padding(.horizontal, SpatailSpace.s2)
                    .padding(.vertical, 2)
                    .background(Capsule().fill(SpatailColor.indigo300))
                    .fixedSize()
                    .offset(y: -20)
            }
            .position(x: box.rect.midX, y: box.rect.midY)
    }
}

/// The plan's binding decision, verbatim from the brain (matchReason included).
private struct BindingCard: View {
    let binding: TruthOverlayModel.BindingInfo

    var body: some View {
        VStack(alignment: .leading, spacing: SpatailSpace.s1) {
            HStack {
                Text("PLAN BINDING · v\(binding.version)")
                    .spatailType(.label)
                    .foregroundStyle(SpatailGlassTone.dark.eyebrow)
                Spacer()
                Text(String(format: "%.2f", binding.confidence))
                    .spatailType(.micro, weight: .medium)
                    .foregroundStyle(SpatailColor.indigo300)
            }
            Text("\(binding.label) → \(binding.boundTo)")
                .spatailType(.base, weight: .medium)
                .foregroundStyle(SpatailColor.paper)
            Text(binding.matchReason)
                .spatailType(.xs)
                .foregroundStyle(SpatailColor.paper.opacity(0.75))
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, SpatailSpace.s4)
        .padding(.vertical, SpatailSpace.s3)
        .frame(maxWidth: .infinity, alignment: .leading)
        .spatailGlass(tone: .dark, radius: SpatailRadius.lg)
    }
}

// MARK: - Model (Combine wiring + 10 Hz label projection)

@MainActor
final class TruthOverlayModel: ObservableObject {

    struct ProjectedLabel: Identifiable, Equatable {
        let id: String
        var text: String
        var point: CGPoint
        var dot: Color
        var accent: Bool
        /// Form Engine truth lines (dimensions + provenance); empty without a form.
        var sublines: [String] = []
    }

    struct VLMBox: Identifiable, Equatable {
        let id: String
        var label: String
        var confidence: Float
        var rect: CGRect
    }

    struct BindingInfo: Equatable {
        var label: String
        var boundTo: String
        var matchReason: String
        var confidence: Double
        var version: Int
    }

    @Published private(set) var surfaceLabels: [ProjectedLabel] = []
    @Published private(set) var objectLabels: [ProjectedLabel] = []
    @Published private(set) var vlmBoxes: [VLMBox] = []
    @Published private(set) var binding: BindingInfo?
    @Published private(set) var legendKinds: [SurfaceKind] = []
    /// Honest count of surfaces the curation filter kept off-screen.
    @Published private(set) var hiddenSurfaceCount = 0

    /// The curated surface list the renderer AND the label tick both draw —
    /// one filter, applied once per scanner publish.
    private var shownSurfaces: [RoomSurface] = []

    private let renderer = TruthOverlayRenderer()
    private var cancellables: Set<AnyCancellable> = []
    private weak var hub: ARSessionHub?
    private weak var scanner: RoomScannerService?
    private weak var registry: ObjectRegistry?

    private var lastIdentification: IdentificationWire?
    private var identificationReceivedAt: Date?
    private var boundSurfaceId: String?
    private var boundObjectId: UUID?
    private var attached = false

    private static let consumerId = "truth.labels"
    /// VLM boxes describe a ~1 Hz identity — draw them briefly, never let them lie stale.
    private static let vlmBoxLifetime: TimeInterval = 4

    // MARK: Wiring

    func attach(hub: ARSessionHub, scanner: RoomScannerService,
                registry: ObjectRegistry, uplink: VisionUplink) {
        guard !attached else { return }
        attached = true
        self.hub = hub
        self.scanner = scanner
        self.registry = registry

        print("[Overlay] attach — scanner \(scanner.tag), " +
              "\(scanner.surfaces.count) surfaces at attach")
        renderer.attach(to: hub.arView)
        applySurfaces(scanner.surfaces)
        renderer.update(objects: registry.objects.filter(\.displayWorthy),
                        pinned: Set(registry.pinnedObjectIds.map(registry.canonicalId)))

        scanner.$surfaces
            .throttle(for: .milliseconds(300), scheduler: DispatchQueue.main, latest: true)
            .sink { [weak self] surfaces in
                guard let self else { return }
                print("[Overlay] renderer gets \(surfaces.count) surfaces")
                self.applySurfaces(surfaces)
            }
            .store(in: &cancellables)

        registry.$objects
            .throttle(for: .milliseconds(250), scheduler: DispatchQueue.main, latest: true)
            .sink { [weak self] objects in
                guard let self else { return }
                // The thoughtful scene: only display-worthy objects draw; the
                // ask/experience-bound (pinned) ones get the accent treatment.
                // Pins resolve through merge aliases so the survivor keeps the
                // accent (same treatment as the 10 Hz label tick).
                let pinned = self.registry.map {
                    Set($0.pinnedObjectIds.map($0.canonicalId))
                } ?? []
                self.renderer.update(objects: objects.filter(\.displayWorthy),
                                     pinned: pinned)
            }
            .store(in: &cancellables)

        uplink.$lastIdentification
            .receive(on: DispatchQueue.main)
            .sink { [weak self] identification in
                guard let self, identification != nil else { return }
                self.lastIdentification = identification
                self.identificationReceivedAt = Date()
            }
            .store(in: &cancellables)

        uplink.$lastExperienceDelta
            .receive(on: DispatchQueue.main)
            .sink { [weak self] delta in
                self?.apply(delta: delta)
            }
            .store(in: &cancellables)

        hub.addFrameConsumer(id: Self.consumerId, hz: 10) { [weak self] frame in
            self?.tick(frame)
        }
    }

    func detach() {
        hub?.removeFrameConsumer(id: Self.consumerId)
        cancellables.removeAll()
        renderer.detach()
        surfaceLabels = []
        objectLabels = []
        vlmBoxes = []
        attached = false
    }

    // MARK: Surface curation (the thoughtful scene, not perception exhaust)

    /// Floor/table/seat always draw; wall/door/window/ceiling (and unknown) only
    /// at ≥ 0.8 m²; fragments under 0.15 m² never draw. One filter for the 3D
    /// renderer AND the 2D labels; the legend reports what it hid.
    static let alwaysDrawnKinds: Set<SurfaceKind> = [.floor, .table, .seat]
    static let minSurfaceArea: Float = 0.15
    static let minorSurfaceArea: Float = 0.8

    static func curatedSurfaces(_ surfaces: [RoomSurface]) -> [RoomSurface] {
        surfaces.filter { surface in
            surface.areaM2 >= minSurfaceArea
                && (alwaysDrawnKinds.contains(surface.kind)
                    || surface.areaM2 >= minorSurfaceArea)
        }
    }

    private func applySurfaces(_ surfaces: [RoomSurface]) {
        let shown = Self.curatedSurfaces(surfaces)
        shownSurfaces = shown
        hiddenSurfaceCount = surfaces.count - shown.count
        renderer.update(surfaces: shown)
        let kinds = Set(shown.map(\.kind))
        legendKinds = SurfaceKind.allCases.filter(kinds.contains)
    }

    // MARK: Binding (experience.delta → highlight + card)

    private func apply(delta: ExperienceDeltaWire?) {
        guard let delta, let fused = delta.fused else {
            binding = nil
            boundSurfaceId = nil
            boundObjectId = nil
            renderer.setBinding(surfaceId: nil, objectId: nil)
            return
        }
        boundObjectId = fused.objectId.flatMap(UUID.init(uuidString:))
        boundSurfaceId = fused.surfaceId
        renderer.setBinding(surfaceId: boundSurfaceId, objectId: boundObjectId)

        let boundTo: String
        if let objectId = boundObjectId {
            let name = registry?.objects.first(where: { $0.id == objectId })?.label
            boundTo = "object · \(name ?? String(objectId.uuidString.prefix(8)))"
        } else if let kind = fused.kind {
            boundTo = "surface · \(kind)"
        } else if let surfaceId = fused.surfaceId {
            boundTo = "surface · \(surfaceId)"
        } else {
            boundTo = "—"
        }
        binding = BindingInfo(label: fused.label ?? "—",
                              boundTo: boundTo,
                              matchReason: fused.matchReason ?? "no matchReason supplied",
                              confidence: fused.confidence ?? 0,
                              version: delta.version)
    }

    // MARK: 10 Hz label + box projection

    private func tick(_ frame: ARFrame) {
        guard let hub, scanner != nil, let registry else { return }
        let arView = hub.arView
        let bounds = arView.bounds
        guard bounds.width > 1, bounds.height > 1 else { return }

        // (a) surface labels at the boundary centroid — CURATED list only
        var sLabels: [ProjectedLabel] = []
        for surface in shownSurfaces {
            guard !surface.boundary.isEmpty else { continue }
            var centroid = surface.boundary.reduce(SIMD3<Float>(0, 0, 0), +)
            centroid /= Float(surface.boundary.count)
            centroid.y = surface.y + 0.02
            guard let p = arView.project(centroid),
                  p.x > -40, p.x < bounds.width + 40,
                  p.y > -40, p.y < bounds.height + 40 else { continue }
            let accent = surface.id == boundSurfaceId
            sLabels.append(ProjectedLabel(
                id: surface.id,
                text: String(format: "%@ · %.2f · %.1fm²",
                             surface.kind.rawValue, surface.confidence, surface.areaM2),
                point: p,
                dot: accent ? TruthPalette.bound : TruthPalette.color(for: surface.kind),
                accent: accent))
        }
        if sLabels != surfaceLabels { surfaceLabels = sLabels }

        // (b) object labels — fused label + confidence + identification age.
        // lastIdentifiedAt lives on the ARKit uptime clock (frame timestamps),
        // so "now" must too — the frame in hand carries it.
        let now = frame.timestamp
        // The ask/experience binding: the delta's fused object OR any pinned
        // object (a placed ask pins its target) gets the accent treatment.
        let pinnedIds = Set(registry.pinnedObjectIds.map(registry.canonicalId))
        var oLabels: [ProjectedLabel] = []
        for object in registry.objects where object.displayWorthy {
            guard let p = arView.project(object.obb.center),
                  p.x > -40, p.x < bounds.width + 40,
                  p.y > -40, p.y < bounds.height + 40 else { continue }
            let accent = object.id == boundObjectId || pinnedIds.contains(object.id)
            var text = "\(object.label ?? "unlabeled") · \(String(format: "%.2f", object.confidence))"
            if let age = object.identificationAge(now: now), age >= 0, age < 3600 {
                text += String(format: " · %.1fs", age)
            }
            oLabels.append(ProjectedLabel(
                id: object.id.uuidString,
                text: text,
                point: p,
                dot: accent ? TruthPalette.bound
                    : (object.label != nil ? TruthPalette.objectLabeled
                                           : TruthPalette.objectUnlabeled),
                accent: accent,
                sublines: object.form.map(Self.formLines) ?? []))
        }
        if oLabels != objectLabels { objectLabels = oLabels }

        // (d) last VLM detections, aspect-fill corrected, aged out after 4 s
        var boxes: [VLMBox] = []
        if let identification = lastIdentification,
           let receivedAt = identificationReceivedAt,
           Date().timeIntervalSince(receivedAt) < Self.vlmBoxLifetime {
            let orientation = WindowChrome.interfaceOrientation
            for (index, detection) in identification.detections.enumerated() {
                guard let b = detection.box, b.count == 4 else { continue }
                let upright = CGRect(x: b[0], y: b[1], width: b[2], height: b[3])
                guard let rect = VisionBoxMapper.viewRect(uprightBox: upright,
                                                          frame: frame,
                                                          viewportSize: bounds.size,
                                                          orientation: orientation)
                else { continue }
                boxes.append(VLMBox(id: "\(index)·\(detection.label)",
                                    label: detection.label,
                                    confidence: Float(detection.confidence),
                                    rect: rect))
            }
        }
        if boxes != vlmBoxes { vlmBoxes = boxes }
    }

    // MARK: Form Engine truth lines (the caliper-verification format)

    /// "⌀ 63 · h 218 · cap ⌀ 31 mm" + "measured · 62% arc" / "prior — transparent?".
    static func formLines(_ form: ObjectForm) -> [String] {
        func mm(_ v: Float) -> String { "\(Int((v * 1000).rounded()))" }

        var dims = ""
        switch form.kind {
        case .revolution:
            if let d = form.bodyDiameter { dims = "⌀ \(mm(d))" }
            if let h = form.height { dims += (dims.isEmpty ? "" : " · ") + "h \(mm(h))" }
            if let cd = form.capDiameter { dims += " · cap ⌀ \(mm(cd))" }
        case .box:
            if let w = form.dimensions["width"], let d = form.dimensions["depth"],
               let h = form.dimensions["height"] {
                dims = "\(mm(w))×\(mm(d))×\(mm(h))"
            }
        }
        guard !dims.isEmpty else { return [] }
        dims += " mm"

        let source: String
        switch form.source {
        case .measured:
            source = "measured · \(Int((form.arcCoverage * 100).rounded()))% arc"
        case .prior:
            source = "prior — transparent?"
        }
        return [dims, source]
    }
}
