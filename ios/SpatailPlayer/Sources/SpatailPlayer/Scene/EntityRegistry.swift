// EntityRegistry.swift
// Maps between USD prim paths, RealityKit entities, and contract element IDs.
// Single source of truth for tap → element resolution.

#if os(iOS) || os(visionOS)
import RealityKit

public final class EntityRegistry {

    private var byPrimPath: [String: Entity] = [:]
    private var byElementId: [String: Entity] = [:]
    private var byEntityId: [Entity.ID: String] = [:]   // Entity.id → elementId

    public init() {}

    public func bind(_ root: Entity, primsIndex: PrimsIndex) {
        byPrimPath.removeAll(keepingCapacity: true)
        byElementId.removeAll(keepingCapacity: true)
        byEntityId.removeAll(keepingCapacity: true)

        // Walk the entity tree and match against the prim index.
        // RealityKit synthesises entity names from USD prim paths when the
        // USDZ exporter sets root_prim_path="/Scene".
        walk(root) { entity in
            let path = "/Scene/" + entity.name
            byPrimPath[path] = entity
            if let elementId = primsIndex.primToElement[path] {
                byElementId[elementId] = entity
                byEntityId[entity.id] = elementId
            }
        }
    }

    public func entity(forElement id: String) -> Entity? {
        byElementId[id]
    }

    public func elementId(for entity: Entity) -> String? {
        byEntityId[entity.id]
    }

    private func walk(_ entity: Entity, _ visit: (Entity) -> Void) {
        visit(entity)
        for child in entity.children {
            walk(child, visit)
        }
    }
}
#endif
