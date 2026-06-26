// PerceptionPlacementRuntime.swift
//
// ════════════════════════════════════════════════════════════════════════
//  "RealityKit renders and anchors the result."
// ════════════════════════════════════════════════════════════════════════
//
// Consumes a SpatailPlacementPlan and materializes it as RealityKit
// entities anchored in the AR world. It is the ONLY perception file that
// turns the plan into geometry — the engine never touches RealityKit.
//
// Lifecycle: each placement owns one AnchorEntity keyed by the placement's
// (stable) id. On every new plan the runtime diffs by id:
//     • id gone        → remove the anchor
//     • id new         → build + add
//     • id seen, moved → reposition (debounced); asset changed → rebuild
// That keeps the blue highlight / label / panel STABLE as the mock re-emits
// the same detection each tick, instead of blinking.
//
// Reuses the app's existing look: HighlightMaterialFactory (the translucent
// blue), LabelEntity (textured text plate), and the brand palette.

import Foundation
import ARKit
import RealityKit
import UIKit
import simd
import Combine

@MainActor
final class PerceptionPlacementRuntime {

    /// One live placement and the bookkeeping to update it in place.
    private struct Managed {
        let anchor: AnchorEntity
        var content: Entity
        var placement: SpatailPlacement
        var worldPosition: SIMD3<Float>
    }

    private weak var arView: ARView?
    private var managed: [String: Managed] = [:]
    /// In-flight USDZ loads, keyed by placement id, retained so they finish.
    private var modelLoads: [String: AnyCancellable] = [:]

    /// Only reposition an anchor when it moves more than this (meters) —
    /// damps the mock's wobble so things don't micro-jitter.
    private let moveThreshold: Float = 0.015

    func attach(to arView: ARView) { self.arView = arView }

    // MARK: - Apply a plan  (the diff)

    func apply(_ plan: SpatailPlacementPlan, cameraPosition: SIMD3<Float>) {
        guard let arView = arView else { return }
        let incoming = Set(plan.placements.map(\.id))

        // 1. Remove anchors whose placement is gone.
        for (id, m) in managed where !incoming.contains(id) {
            arView.scene.removeAnchor(m.anchor)
            modelLoads[id]?.cancel()
            modelLoads[id] = nil
            managed[id] = nil
        }

        // 2. Add or update.
        for placement in plan.placements {
            let worldPos = placement.target.worldPosition
                + worldOffset(placement.placement.offsetMeters,
                              target: placement.target,
                              cameraPosition: cameraPosition)

            if var existing = managed[placement.id] {
                // Rebuild content if the asset instruction changed.
                if existing.placement.asset != placement.asset {
                    existing.anchor.removeChild(existing.content)
                    let content = buildContent(for: placement)
                    existing.anchor.addChild(content)
                    existing.content = content
                }
                // Reposition if moved enough.
                if simd_distance(existing.worldPosition, worldPos) > moveThreshold {
                    existing.anchor.position = worldPos
                    existing.worldPosition = worldPos
                }
                existing.placement = placement
                applyStaticOrientation(existing.content, placement: placement)
                managed[placement.id] = existing
            } else {
                // Anchor at the world ORIGIN and carry the position in the
                // anchor's local transform. This makes `anchor.position` mean
                // the SAME thing (a world coordinate) on both the create and
                // update paths — writing worldPos into a `world: worldPos`
                // anchor's local position would otherwise double it.
                let anchor = AnchorEntity(world: .zero)
                let content = buildContent(for: placement)
                anchor.addChild(content)
                applyStaticOrientation(content, placement: placement)
                anchor.position = worldPos
                arView.scene.addAnchor(anchor)
                managed[placement.id] = Managed(
                    anchor: anchor, content: content,
                    placement: placement, worldPosition: worldPos,
                )
            }
        }
    }

    /// Per-frame: yaw billboarded content toward the camera so labels and
    /// panels stay readable as the user moves.
    func updateBillboards(cameraPosition: SIMD3<Float>) {
        for (_, m) in managed {
            switch m.placement.placement.alignment {
            case .billboard, .uprightFacingUser:
                let d = cameraPosition - m.worldPosition
                guard simd_length(SIMD2(d.x, d.z)) > 1e-4 else { continue }
                // Rotate the plane's +Z to face the camera (yaw only → stays upright).
                m.content.orientation = simd_quatf(angle: atan2(d.x, d.z), axis: SIMD3<Float>(0, 1, 0))
            case .surfaceAligned, .worldFixed:
                break  // fixed orientation set once in applyStaticOrientation
            }
        }
    }

    func clear() {
        guard let arView = arView else { return }
        for (_, m) in managed { arView.scene.removeAnchor(m.anchor) }
        managed.removeAll()
        modelLoads.values.forEach { $0.cancel() }
        modelLoads.removeAll()
    }

    var activePlacementCount: Int { managed.count }

    // MARK: - Orientation

    private func applyStaticOrientation(_ content: Entity, placement: SpatailPlacement) {
        switch placement.placement.alignment {
        case .surfaceAligned:
            // Align the content's +Y to the surface normal so a highlight
            // lies flat on a table / against a wall. Guard the (anti)parallel
            // cases — simd_quatf(from:to:) is NaN when the vectors are
            // antiparallel (reachable via the camera-ray fallback normal).
            if let n = placement.target.surfaceNormal {
                let dest = simd_normalize(n)
                let up = SIMD3<Float>(0, 1, 0)
                let d = simd_dot(up, dest)
                if d > 0.9999 {
                    content.orientation = simd_quatf(ix: 0, iy: 0, iz: 0, r: 1)
                } else if d < -0.9999 {
                    content.orientation = simd_quatf(angle: .pi, axis: SIMD3<Float>(1, 0, 0))
                } else {
                    content.orientation = simd_quatf(from: up, to: dest)
                }
            }
        case .worldFixed:
            content.orientation = simd_quatf(ix: 0, iy: 0, iz: 0, r: 1)
        case .billboard, .uprightFacingUser:
            break  // handled per-frame by updateBillboards
        }
    }

    // MARK: - Local→world offset basis (place "beside" / "above" the target)

    /// Interpret a placement offset (x = right, y = up, z = toward camera) in
    /// a world basis built from world-up and the horizontal direction back to
    /// the camera, so "next to it" lands to the user's side of the object.
    private func worldOffset(_ local: SIMD3<Float>,
                             target: SpatailPlacementTarget,
                             cameraPosition: SIMD3<Float>) -> SIMD3<Float> {
        let up = SIMD3<Float>(0, 1, 0)
        var toward = cameraPosition - target.worldPosition
        toward.y = 0
        let forward = simd_length(toward) > 1e-4 ? simd_normalize(toward) : SIMD3<Float>(0, 0, 1)
        let right = simd_normalize(simd_cross(up, forward))
        return right * local.x + up * local.y + forward * local.z
    }

    // MARK: - Content builders (one per overlay kind)

    private func buildContent(for placement: SpatailPlacement) -> Entity {
        let tint = UIColor(spatailHex: placement.asset.tintHex) ?? .spatailAccentUIColor
        switch placement.asset.overlayKind {
        case .highlight:     return makeHighlight(placement, tint: tint)
        case .label:         return makeLabel(placement, tint: tint)
        case .panel:         return makePanel(placement, tint: tint)
        case .marker:        return makeMarker(placement, tint: tint)
        case .arrow:         return makeArrow(placement, tint: tint)
        case .model,
             .explodedModel: return makeModel(placement, tint: tint)
        }
    }

    /// The blue holographic highlight — reuses the app's HighlightMaterialFactory.
    private func makeHighlight(_ placement: SpatailPlacement, tint: UIColor) -> Entity {
        let s = placement.placement.sizeMeters ?? SIMD3(0.2, 0.2, 0.2)
        let group = Entity()
        let body = ModelEntity(
            mesh: .generateBox(size: s, cornerRadius: min(s.x, s.y, s.z) * 0.12),
            materials: [HighlightMaterialFactory.translucentBlue()],
        )
        group.addChild(body)
        // Soft halo for the "hologram" read.
        let halo = ModelEntity(
            mesh: .generateBox(size: s * 1.18, cornerRadius: min(s.x, s.y, s.z) * 0.16),
            materials: [HighlightMaterialFactory.halo()],
        )
        group.addChild(halo)
        return group
    }

    private func makeLabel(_ placement: SpatailPlacement, tint: UIColor) -> Entity {
        let size = placement.placement.sizeMeters ?? SIMD3(0.3, 0.1, 0.01)
        let text = placement.asset.title ?? placement.target.label
        return LabelEntity.make(
            text: text,
            widthMeters: size.x,
            heightMeters: size.y,
            textColor: tint,                                   // honor the engine's brand tint
            bgColor: UIColor.spatailBgUIColor.withAlphaComponent(0.88),
        )
    }

    /// A multi-line explanation panel rendered to a texture (brand styled).
    private func makePanel(_ placement: SpatailPlacement, tint: UIColor) -> Entity {
        let size = placement.placement.sizeMeters ?? SIMD3(0.42, 0.30, 0.01)
        let image = PerceptionPanelImage.render(
            title: placement.asset.title ?? placement.target.label,
            bodyLines: placement.asset.bodyLines ?? [],
            widthMeters: size.x, heightMeters: size.y, accent: tint,
        )
        let mesh = MeshResource.generatePlane(width: size.x, height: size.y, cornerRadius: 0.012)
        var mat = UnlitMaterial()
        if let cg = image.cgImage,
           let tex = try? TextureResource.generate(from: cg, withName: nil, options: .init(semantic: .color)) {
            mat.color = .init(tint: .white, texture: MaterialParameters.Texture(tex))
        } else {
            mat.color = .init(tint: tint)
        }
        mat.blending = .transparent(opacity: .init(scale: 1.0))
        return ModelEntity(mesh: mesh, materials: [mat])
    }

    private func makeMarker(_ placement: SpatailPlacement, tint: UIColor) -> Entity {
        let r = (placement.placement.sizeMeters?.x ?? 0.05) / 2
        var mat = UnlitMaterial(color: tint)
        mat.blending = .transparent(opacity: .init(scale: 1.0))
        return ModelEntity(mesh: .generateSphere(radius: max(0.012, r)), materials: [mat])
    }

    private func makeArrow(_ placement: SpatailPlacement, tint: UIColor) -> Entity {
        // Note: MeshResource.generateCone is iOS 18+. Stay iOS-16-safe by
        // building the arrowhead from a small box (audit-approved primitive).
        let group = Entity()
        let shaftMat = SimpleMaterial(color: tint, roughness: 0.3, isMetallic: false)
        let shaft = ModelEntity(mesh: .generateCylinder(height: 0.12, radius: 0.004), materials: [shaftMat])
        let head = ModelEntity(
            mesh: .generateBox(size: SIMD3<Float>(0.022, 0.03, 0.022), cornerRadius: 0.004),
            materials: [shaftMat],
        )
        head.position.y = 0.08
        group.addChild(shaft)
        group.addChild(head)
        return group
    }

    /// A model overlay: shows a placeholder immediately, then asynchronously
    /// loads the real USDZ (when `assetRef` is set), fits + grounds it, and
    /// swaps it in. No asset ref → the placeholder stays.
    private func makeModel(_ placement: SpatailPlacement, tint: UIColor) -> Entity {
        let s = placement.placement.sizeMeters ?? SIMD3(0.2, 0.2, 0.2)
        let container = Entity()
        let placeholder = ModelEntity(
            mesh: .generateBox(size: s, cornerRadius: 0.01),
            materials: [SimpleMaterial(color: tint.withAlphaComponent(0.9), roughness: 0.5, isMetallic: false)],
        )
        placeholder.name = "placeholder"
        placeholder.position.y = s.y / 2
        container.addChild(placeholder)

        guard let ref = placement.asset.assetRef else { return container }

        // Resolve a bundle resource name vs. a file URL (e.g. a downloaded
        // asset in Documents). An absolute path (Documents) starts with "/";
        // a bundle resource name does not. loadModelAsync streams off-thread.
        let request: LoadRequest<ModelEntity> = ref.hasPrefix("/")
            ? Entity.loadModelAsync(contentsOf: URL(fileURLWithPath: ref))
            : Entity.loadModelAsync(named: ref)

        let id = placement.id
        let target = s
        modelLoads[id]?.cancel()
        modelLoads[id] = request
            .receive(on: DispatchQueue.main)   // mutate the scene on main
            .sink(receiveCompletion: { [weak self] completion in
                if case .failure(let error) = completion {
                    print("[PerceptionPlacementRuntime] USDZ load failed for \(ref): \(error)")
                }
                self?.modelLoads[id] = nil
            }, receiveValue: { [weak container] model in
                guard let container = container else { return }
                Self.fitAndGround(model, into: target)
                container.children.filter { $0.name == "placeholder" }.forEach { $0.removeFromParent() }
                container.addChild(model)
            })
        return container
    }

    /// Uniformly scale a loaded model so its largest extent matches the
    /// target size, then sit it on the surface (min.y at 0).
    private static func fitAndGround(_ model: ModelEntity, into target: SIMD3<Float>) {
        let bounds = model.visualBounds(relativeTo: nil)
        let extents = bounds.extents
        let maxExtent = max(extents.x, max(extents.y, extents.z, 0.0001))
        let targetMax = max(target.x, max(target.y, target.z))
        let scale = targetMax / maxExtent
        model.scale = SIMD3<Float>(repeating: scale)
        model.position.y = -bounds.min.y * scale
    }
}

// ---------------------------------------------------------------------------
// Panel rasterizer — small, standalone, brand-styled (mirrors the app's
// PanelTextureRenderer but takes plain strings instead of a SpatialElement).
// ---------------------------------------------------------------------------

enum PerceptionPanelImage {
    static func render(title: String, bodyLines: [String],
                       widthMeters: Float, heightMeters: Float,
                       accent: UIColor) -> UIImage {
        let pxPerMeter: CGFloat = 520
        let size = CGSize(width: CGFloat(widthMeters) * pxPerMeter,
                          height: CGFloat(heightMeters) * pxPerMeter)
        return UIGraphicsImageRenderer(size: size).image { _ in
            // Card — brand panel surface (mirrors Color.spatailBgPanel).
            let radius: CGFloat = 26
            let card = UIBezierPath(roundedRect: CGRect(origin: .zero, size: size), cornerRadius: radius)
            UIColor.spatailBgPanelUIColor.withAlphaComponent(0.96).setFill(); card.fill()
            accent.withAlphaComponent(0.4).setStroke(); card.lineWidth = 3; card.stroke()
            // Accent stripe.
            accent.setFill()
            UIBezierPath(rect: CGRect(x: 0, y: 0, width: 8, height: size.height)).fill()

            let pad: CGFloat = 26
            let titleFont = UIFont.systemFont(ofSize: size.height * 0.11, weight: .bold)
            (title as NSString).draw(
                in: CGRect(x: pad, y: pad, width: size.width - 2 * pad, height: size.height * 0.2),
                withAttributes: [.font: titleFont, .foregroundColor: UIColor.white],
            )
            var y = pad + titleFont.lineHeight + 12
            let bodyFont = UIFont.systemFont(ofSize: size.height * 0.066, weight: .medium)
            for line in bodyLines {
                (line as NSString).draw(
                    in: CGRect(x: pad, y: y, width: size.width - 2 * pad, height: bodyFont.lineHeight + 4),
                    withAttributes: [.font: bodyFont, .foregroundColor: UIColor(white: 0.72, alpha: 1.0)],
                )
                y += bodyFont.lineHeight + 8
                if y > size.height - pad { break }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Color helpers
// ---------------------------------------------------------------------------

extension UIColor {
    /// The app's brand colors in UIKit form (mirror the Color.spatail* palette
    /// in AppRoot so in-world textures stay on-brand without magic numbers).
    static let spatailAccentUIColor   = UIColor(red: 0.43, green: 0.66, blue: 1.0, alpha: 1.0)
    static let spatailBgUIColor       = UIColor(red: 0.05, green: 0.05, blue: 0.06, alpha: 1.0)
    static let spatailBgPanelUIColor  = UIColor(red: 0.10, green: 0.11, blue: 0.14, alpha: 1.0)

    /// Parse "#RRGGBB" / "#RRGGBBAA". Returns nil on malformed input.
    convenience init?(spatailHex: String?) {
        guard var hex = spatailHex else { return nil }
        if hex.hasPrefix("#") { hex.removeFirst() }
        guard hex.count == 6 || hex.count == 8,
              let value = UInt64(hex, radix: 16) else { return nil }
        let r, g, b, a: CGFloat
        if hex.count == 8 {
            r = CGFloat((value >> 24) & 0xFF) / 255
            g = CGFloat((value >> 16) & 0xFF) / 255
            b = CGFloat((value >> 8) & 0xFF) / 255
            a = CGFloat(value & 0xFF) / 255
        } else {
            r = CGFloat((value >> 16) & 0xFF) / 255
            g = CGFloat((value >> 8) & 0xFF) / 255
            b = CGFloat(value & 0xFF) / 255
            a = 1
        }
        self.init(red: r, green: g, blue: b, alpha: a)
    }
}
