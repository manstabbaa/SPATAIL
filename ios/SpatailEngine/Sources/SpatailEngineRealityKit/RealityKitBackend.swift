import Foundation
import SpatailEngineCore

// The iOS/visionOS adapter: the ONLY place the engine touches a rendering framework.
// It implements `SceneBackend` by mapping the core's commands onto RealityKit and
// feeds RealityKit/host events back into the engine. To port SPATAIL to another
// platform you reimplement this one file; the core is untouched.
//
// Guarded on `canImport(UIKit)` so the package still `swift build`/`swift test`s on
// macOS (where RealityKit exists but UIKit/ARKit don't) — there the target is inert
// and the pure core tests run. The real adapter builds in Xcode for iOS/visionOS.

#if canImport(RealityKit) && canImport(UIKit)
import RealityKit
import Combine
import UIKit
import simd

public final class RealityKitBackend: SceneBackend {

    /// Attach the engine's content under this root (your AnchorEntity / object anchor).
    public let root = RealityKit.Entity()

    /// Host injects how to turn an asset URL string into a *local* file URL to load
    /// (the app already downloads USDZs in ModularRuntime; reuse that). Return nil to
    /// fall back to the primitive.
    public var resolveAssetURL: ((String) -> URL?)?
    public var onSpeak: ((String) -> Void)?
    public var onSound: ((String) -> Void)?
    public var onHUD: (([String: Value]) -> Void)?
    public var onAnchor: ((AnchorSpec) -> Void)?
    /// Host pushes engine events back here (it owns the SpatailEngine instance).
    public var emit: ((EngineEvent) -> Void)?

    private var nodes: [EntityID: RealityKit.Entity] = [:]
    private var idByEntity: [ObjectIdentifier: EntityID] = [:]
    private var savedMaterials: [EntityID: [Material]] = [:]

    public init() {}

    // MARK: SceneBackend

    public func apply(_ command: BackendCommand) {
        switch command {
        case let .createVisual(id, spec):   createVisual(id, spec)
        case let .updateTransform(id, t):   nodes[id]?.transform = rk(t)
        case let .removeVisual(id):         removeVisual(id)
        case let .playClip(id, name, loop): playClip(id, name: name, loop: loop)
        case let .stopClip(id):             nodes[id]?.stopAllAnimations()
        case let .applyEffect(target, fx):  applyEffect(target, fx)
        case let .clearEffect(target):      clearEffect(target)
        case let .addPhysics(id, p):        addPhysics(id, p)
        case let .applyImpulse(id, v):      applyImpulse(id, v)
        case let .raycast(rid, o, d, mask): raycast(rid, origin: o, dir: d, mask: mask)
        case let .anchor(spec):             onAnchor?(spec)
        case let .speak(text):              onSpeak?(text)
        case let .playSound(name):          onSound?(name)
        case let .hud(spec):                onHUD?(spec.fields)
        }
    }

    // MARK: host → engine

    /// Call from your ARView tap handler with the hit RealityKit entity (or nil).
    public func reportTap(on rkEntity: RealityKit.Entity?) {
        emit?(EngineEvent(Ev.tap, entity: rkEntity.flatMap { mappedID(for: $0) }))
    }

    /// Subscribe to RealityKit collisions so the shooter's hit rule fires. Convention:
    /// the projectile-named body becomes `entity`, the other becomes `other`.
    public func attachCollisions(to scene: Scene) -> Cancellable {
        scene.subscribe(to: CollisionEvents.Began.self) { [weak self] ev in
            guard let self, let a = self.mappedID(for: ev.entityA), let b = self.mappedID(for: ev.entityB) else { return }
            let aIsProj = ev.entityA.name.contains("projectile")
            let (proj, hit) = aIsProj ? (a, b) : (b, a)
            self.emit?(EngineEvent(Ev.collision, entity: proj, other: hit,
                                   payload: ["point": .list([.number(Double(ev.position.x)),
                                                             .number(Double(ev.position.y)),
                                                             .number(Double(ev.position.z))])]))
        }
    }

    // MARK: command impls

    private func createVisual(_ id: EntityID, _ spec: VisualSpec) {
        let entity: RealityKit.Entity
        if let urlStr = spec.assetUSDZ, let url = resolveAssetURL?(urlStr), let loaded = try? RealityKit.Entity.load(contentsOf: url) {
            entity = loaded
            fit(loaded, spec)
        } else {
            entity = makePrimitive(spec)
        }
        entity.name = "e\(id.raw)"
        if spec.hittable { entity.generateCollisionShapes(recursive: true) }
        register(entity, as: id)
        root.addChild(entity)
    }

    private func makePrimitive(_ spec: VisualSpec) -> ModelEntity {
        let s = spec.size
        let mesh: MeshResource
        switch spec.primitive ?? "box" {
        case "sphere":   mesh = .generateSphere(radius: max(s.x, max(s.y, s.z)) * 0.5)
        case "cylinder": if #available(iOS 18.0, visionOS 1.0, *) { mesh = .generateCylinder(height: s.y, radius: s.x * 0.5) } else { mesh = .generateBox(size: [s.x, s.y, s.z]) }
        case "plane":    mesh = .generatePlane(width: s.x, depth: s.z)
        default:         mesh = .generateBox(size: [s.x, s.y, s.z])
        }
        let color = parseColor(spec.color) ?? UIColor(white: 0.7, alpha: 1)
        return ModelEntity(mesh: mesh, materials: [SimpleMaterial(color: color, isMetallic: false)])
    }

    private func fit(_ e: RealityKit.Entity, _ spec: VisualSpec) {
        if spec.realScaleBaked { e.scale = .one; return }
        let b = e.visualBounds(relativeTo: nil)
        let maxDim = max(b.extents.x, max(b.extents.y, b.extents.z))
        guard maxDim > 0 else { return }
        let target = spec.realSizeMeters.map { max($0.x, max($0.y, $0.z)) } ?? max(spec.size.x, max(spec.size.y, spec.size.z))
        e.scale = SIMD3(repeating: target / maxDim)
    }

    private func removeVisual(_ id: EntityID) {
        if let e = nodes[id] {
            idByEntity[ObjectIdentifier(e)] = nil
            e.removeFromParent()
        }
        nodes[id] = nil
        savedMaterials[id] = nil
    }

    private func playClip(_ id: EntityID, name: String, loop: Bool) {
        guard let e = nodes[id] else { return }
        func play(_ x: RealityKit.Entity) {
            for a in x.availableAnimations {
                x.playAnimation(loop ? a.repeat() : a, transitionDuration: 0.2, startsPaused: false)
            }
            x.children.forEach(play)
        }
        play(e)
    }

    private func addPhysics(_ id: EntityID, _ p: PhysicsSpec) {
        guard let model = (nodes[id] as? ModelEntity) ?? nodes[id]?.children.compactMap({ $0 as? ModelEntity }).first else { return }
        let (friction, restitution): (Float, Float)
        switch p.body {
        case "bouncy": (friction, restitution) = (0.3, 0.8)
        case "heavy":  (friction, restitution) = (0.9, 0.1)
        case "soft":   (friction, restitution) = (0.8, 0.2)
        default:       (friction, restitution) = (0.7, 0.4)
        }
        let mat = PhysicsMaterialResource.generate(friction: friction, restitution: restitution)
        let mode: PhysicsBodyMode = (p.mode == "static") ? .static : (p.mode == "kinematic" ? .kinematic : .dynamic)
        model.components.set(PhysicsBodyComponent(massProperties: .init(mass: p.mass), material: mat, mode: mode))
        model.components.set(CollisionComponent(shapes: [.generateBox(size: model.visualBounds(relativeTo: nil).extents)]))
    }

    private func applyImpulse(_ id: EntityID, _ v: Vec3) {
        (nodes[id] as? ModelEntity)?.applyLinearImpulse(SIMD3(v.x, v.y, v.z), relativeTo: nil)
    }

    private func raycast(_ rid: String, origin: Vec3, dir: Vec3, mask: [String]) {
        // TODO(app): the host owns the ARView/scene — perform the raycast there and, on
        // a hit, call emit?(EngineEvent(Ev.raycastHit, entity: hitID, payload: ["id": .text(rid)])).
    }

    // MARK: minimal non-destructive effects (fold in the app's SpatailMaterials later)

    private func applyEffect(_ target: EffectTarget, _ fx: VisualEffect) {
        guard let model = modelEntity(unpack(target).0) else { return }
        let id = unpack(target).0
        if savedMaterials[id] == nil { savedMaterials[id] = model.model?.materials ?? [] }
        switch fx.name {
        case "ghost":
            if #available(iOS 18.0, visionOS 1.0, *) { model.components.set(OpacityComponent(opacity: 0.3)) }
        case "tint":
            if let c = parseColor(fx.hex) { model.model?.materials = [SimpleMaterial(color: c, isMetallic: false)] }
        default: // highlight / emissive
            var m = PhysicallyBasedMaterial()
            m.emissiveColor = .init(color: .white)
            m.emissiveIntensity = (fx.name == "emissive") ? 2.0 : 0.6
            if let base = (model.model?.materials.first as? PhysicallyBasedMaterial)?.baseColor { m.baseColor = base }
            model.model?.materials = [m]
        }
    }

    private func clearEffect(_ target: EffectTarget) {
        let id = unpack(target).0
        guard let model = modelEntity(id) else { return }
        if #available(iOS 18.0, visionOS 1.0, *) { model.components.remove(OpacityComponent.self) }
        if let saved = savedMaterials[id] { model.model?.materials = saved; savedMaterials[id] = nil }
    }

    // MARK: helpers

    private func register(_ e: RealityKit.Entity, as id: EntityID) {
        nodes[id] = e
        idByEntity[ObjectIdentifier(e)] = id
    }
    private func mappedID(for e: RealityKit.Entity) -> EntityID? {
        var cur: RealityKit.Entity? = e
        while let c = cur { if let id = idByEntity[ObjectIdentifier(c)] { return id }; cur = c.parent }
        return nil
    }
    private func modelEntity(_ id: EntityID) -> ModelEntity? {
        (nodes[id] as? ModelEntity) ?? nodes[id]?.children.compactMap { $0 as? ModelEntity }.first
    }
    private func unpack(_ t: EffectTarget) -> (EntityID, String?) {
        switch t { case .entity(let id): return (id, nil); case .region(let id, let r): return (id, r) }
    }
    private func rk(_ t: SpatailEngineCore.Transform) -> RealityKit.Transform {
        RealityKit.Transform(scale: SIMD3(t.scale.x, t.scale.y, t.scale.z),
                             rotation: simd_quatf(ix: t.rotation.x, iy: t.rotation.y, iz: t.rotation.z, r: t.rotation.w),
                             translation: SIMD3(t.position.x, t.position.y, t.position.z))
    }
    private func parseColor(_ hex: String?) -> UIColor? {
        guard var h = hex else { return nil }
        if h.hasPrefix("#") { h.removeFirst() }
        guard h.count == 6, let v = UInt32(h, radix: 16) else { return nil }
        return UIColor(red: CGFloat((v >> 16) & 0xff) / 255,
                       green: CGFloat((v >> 8) & 0xff) / 255,
                       blue: CGFloat(v & 0xff) / 255, alpha: 1)
    }
}

#else
/// Non-Apple / no-UIKit platforms (incl. macOS `swift test`): the package still
/// compiles; this target is inert so the pure core can be built and tested.
public final class RealityKitBackend {
    public init() {}
}
#endif
