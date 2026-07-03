// TruthOverlayRenderer.swift — the 3D half of the verification instrument.
//
// Renders, inside the live ARView:
//   • room-surface boundaries as outline POLYLINES from RoomSurface.boundary —
//     the true (possibly concave) outline the scanner published, never a convex
//     slab (the old EngineViewer blockout hid concavity; this draws the truth);
//   • object OBB wireframes (12 edges) from the registry, plus each resolved
//     part region as a thinner sub-box — the bottle-cap test made visible;
//   • the current plan's binding highlighted in the one accent color.
//
// DIFF-BASED by id (spec §0 law 2): every surface/object keeps its own container
// entity keyed by id with a change signature; only entities whose geometry
// actually changed are rebuilt, transforms are updated in place, and removals
// are per-id. Never removeAll-and-rebuild per tick.
//
// All meshes share ONE unit-box MeshResource; segments are non-uniform scales of
// it, so a full room costs one mesh allocation total.

import Foundation
import ARKit
import RealityKit
import SwiftUI
import UIKit
import simd

// MARK: - Palette (single source for 3D materials AND the SwiftUI legend)

enum TruthPalette {
    /// Surface-kind color code. Tokens only — the legend chip shows the same map.
    static func color(for kind: SurfaceKind) -> Color {
        switch kind {
        case .floor:   return SpatailColor.gray500
        case .wall:    return SpatailColor.gray300
        case .ceiling: return SpatailColor.gray600
        case .table:   return SpatailColor.indigo300
        case .seat:    return SpatailColor.statusWarning
        case .door:    return SpatailColor.statusDanger
        case .window:  return SpatailColor.statusInfo
        case .unknown: return SpatailColor.gray400
        }
    }

    static let bound = SpatailColor.indigo500          // the one accent — binding only
    static let objectLabeled = SpatailColor.gray100
    static let objectUnlabeled = SpatailColor.gray500
    static let partRegion = SpatailColor.indigo300
}

// MARK: - Renderer

@MainActor
final class TruthOverlayRenderer {

    private static let unitBox = MeshResource.generateBox(size: 1)

    private let root = AnchorEntity(world: SIMD3<Float>(0, 0, 0))
    private weak var arView: ARView?

    private struct Node {
        let entity: Entity
        var signature: String
    }

    private var surfaceNodes: [String: Node] = [:]
    private var objectNodes: [UUID: Node] = [:]
    private var lastSurfaces: [RoomSurface] = []
    private var lastObjects: [SpatailObject] = []
    private var boundSurfaceId: String?
    private var boundObjectId: UUID?

    // MARK: Lifecycle

    func attach(to arView: ARView) {
        guard root.parent == nil else { return }
        self.arView = arView
        arView.scene.addAnchor(root)
    }

    func detach() {
        root.children.removeAll()
        root.removeFromParent()
        surfaceNodes.removeAll()
        objectNodes.removeAll()
        lastSurfaces = []
        lastObjects = []
    }

    // MARK: Binding highlight (plan → world)

    /// Highlight the surface/object the current plan bound to. Signatures carry
    /// the bound flag, so only the entities whose highlight changed rebuild.
    func setBinding(surfaceId: String?, objectId: UUID?) {
        guard surfaceId != boundSurfaceId || objectId != boundObjectId else { return }
        boundSurfaceId = surfaceId
        boundObjectId = objectId
        update(surfaces: lastSurfaces)
        update(objects: lastObjects)
    }

    // MARK: Surfaces (concave boundary polylines)

    func update(surfaces: [RoomSurface]) {
        lastSurfaces = surfaces
        var seen = Set<String>()

        for surface in surfaces {
            seen.insert(surface.id)
            let bound = surface.id == boundSurfaceId
            let sig = Self.signature(for: surface, bound: bound)
            if let node = surfaceNodes[surface.id] {
                if node.signature != sig {
                    rebuildSurface(node.entity, surface, bound: bound)
                    surfaceNodes[surface.id]?.signature = sig
                }
            } else {
                let container = Entity()
                root.addChild(container)
                rebuildSurface(container, surface, bound: bound)
                surfaceNodes[surface.id] = Node(entity: container, signature: sig)
            }
        }

        for (id, node) in surfaceNodes where !seen.contains(id) {
            node.entity.removeFromParent()
            surfaceNodes[id] = nil
        }
    }

    private func rebuildSurface(_ container: Entity, _ surface: RoomSurface, bound: Bool) {
        container.children.removeAll()
        let points = surface.boundary
        guard points.count >= 2 else { return }

        let color = bound ? TruthPalette.bound : TruthPalette.color(for: surface.kind)
        let material = UnlitMaterial(color: UIColor(color))
        let thickness: Float = bound ? 0.009 : 0.005

        // Closed outline: draw exactly what the scanner published, point to point.
        for i in 0..<points.count {
            let a = points[i]
            let b = points[(i + 1) % points.count]
            if let seg = Self.segment(from: a, to: b, thickness: thickness, material: material) {
                container.addChild(seg)
            }
        }
    }

    // MARK: Objects (OBB wireframes + part regions)

    func update(objects: [SpatailObject]) {
        lastObjects = objects
        var seen = Set<UUID>()

        for object in objects {
            seen.insert(object.id)
            let bound = object.id == boundObjectId
            let sig = Self.signature(for: object, bound: bound)
            let node: Node
            if let existing = objectNodes[object.id] {
                node = existing
                if existing.signature != sig {
                    rebuildObject(existing.entity, object, bound: bound)
                    objectNodes[object.id]?.signature = sig
                }
            } else {
                let container = Entity()
                root.addChild(container)
                rebuildObject(container, object, bound: bound)
                node = Node(entity: container, signature: sig)
                objectNodes[object.id] = node
            }
            // Transform updates are always in place — the OBB rides ARKit.
            node.entity.position = object.obb.center
            node.entity.orientation = simd_quatf(angle: object.obb.yaw, axis: SIMD3<Float>(0, 1, 0))
        }

        for (id, node) in objectNodes where !seen.contains(id) {
            node.entity.removeFromParent()
            objectNodes[id] = nil
        }
    }

    private func rebuildObject(_ container: Entity, _ object: SpatailObject, bound: Bool) {
        container.children.removeAll()

        let color: Color = bound ? TruthPalette.bound
            : (object.label != nil ? TruthPalette.objectLabeled : TruthPalette.objectUnlabeled)
        let material = UnlitMaterial(color: UIColor(color))
        let thickness: Float = bound ? 0.007 : 0.0035

        for edge in Self.boxEdges(extents: object.obb.extents,
                                  center: .zero,
                                  thickness: thickness,
                                  material: material) {
            container.addChild(edge)
        }

        // Part regions — resolved sub-OBBs, drawn in the parent's local frame.
        // (Resolver clamps regions inside the parent and shares its yaw, spec §3.)
        let partMaterial = UnlitMaterial(color: UIColor(TruthPalette.partRegion))
        let inverseYaw = simd_quatf(angle: -object.obb.yaw, axis: SIMD3<Float>(0, 1, 0))
        for part in object.parts {
            guard let region = part.region else { continue }
            let localCenter = inverseYaw.act(region.center - object.obb.center)
            for edge in Self.boxEdges(extents: region.extents,
                                      center: localCenter,
                                      thickness: 0.0025,
                                      material: partMaterial) {
                container.addChild(edge)
            }
        }
    }

    // MARK: Geometry helpers

    /// A line segment as a non-uniform scale of the shared unit box.
    private static func segment(from a: SIMD3<Float>, to b: SIMD3<Float>,
                                thickness: Float, material: UnlitMaterial) -> ModelEntity? {
        let d = b - a
        let length = simd_length(d)
        guard length > 0.002 else { return nil }
        let dir = d / length

        let entity = ModelEntity(mesh: unitBox, materials: [material])
        entity.scale = SIMD3<Float>(length, thickness, thickness)
        entity.position = (a + b) / 2
        let xAxis = SIMD3<Float>(1, 0, 0)
        if simd_dot(xAxis, dir) < -0.9999 {
            entity.orientation = simd_quatf(angle: .pi, axis: SIMD3<Float>(0, 1, 0))
        } else {
            entity.orientation = simd_quatf(from: xAxis, to: dir)
        }
        return entity
    }

    /// The 12 edges of an axis-aligned box (local space) around `center`.
    private static func boxEdges(extents: SIMD3<Float>, center: SIMD3<Float>,
                                 thickness: Float, material: UnlitMaterial) -> [ModelEntity] {
        let h = extents / 2
        var edges: [ModelEntity] = []
        let signs: [Float] = [-1, 1]

        for sy in signs { for sz in signs {
            if let e = segment(from: center + SIMD3(-h.x, sy * h.y, sz * h.z),
                               to: center + SIMD3(h.x, sy * h.y, sz * h.z),
                               thickness: thickness, material: material) { edges.append(e) }
        }}
        for sx in signs { for sz in signs {
            if let e = segment(from: center + SIMD3(sx * h.x, -h.y, sz * h.z),
                               to: center + SIMD3(sx * h.x, h.y, sz * h.z),
                               thickness: thickness, material: material) { edges.append(e) }
        }}
        for sx in signs { for sy in signs {
            if let e = segment(from: center + SIMD3(sx * h.x, sy * h.y, -h.z),
                               to: center + SIMD3(sx * h.x, sy * h.y, h.z),
                               thickness: thickness, material: material) { edges.append(e) }
        }}
        return edges
    }

    // MARK: Change signatures (cheap, geometry-sensitive)

    private static func signature(for s: RoomSurface, bound: Bool) -> String {
        var acc: Float = 0
        for p in s.boundary { acc += p.x * 3.7 + p.y * 5.1 + p.z * 7.3 }
        return "\(s.kind.rawValue)|\(s.boundary.count)|\(Int(s.y * 1000))|" +
               "\(Int(s.areaM2 * 100))|\(Int(acc * 100))|\(bound)"
    }

    private static func signature(for o: SpatailObject, bound: Bool) -> String {
        let e = o.obb.extents
        var parts = ""
        for p in o.parts {
            guard let r = p.region else { continue }
            parts += "\(p.label)\(Int(r.center.y * 200))\(Int(r.extents.y * 200))"
        }
        return "\(Int(e.x * 200))|\(Int(e.y * 200))|\(Int(e.z * 200))|" +
               "\(o.label ?? "·")|\(parts)|\(bound)"
    }
}
