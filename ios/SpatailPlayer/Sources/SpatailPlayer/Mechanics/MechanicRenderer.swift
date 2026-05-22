// MechanicRenderer.swift
// Protocol every mechanic renderer implements. v1 ships 7 mechanics
// (see docs/xr/IOS_BUNDLE_SPEC.md §5); the rest fall back to
// PlaceholderRenderer.

#if canImport(RealityKit)
import RealityKit

public protocol MechanicRenderer: AnyObject {
    /// Closed-vocab key matching `MechanicKind` in Vocab.swift.
    static var kind: MechanicKind { get }

    func attach(to root: Entity,
                params: AnyCodable?,
                registry: EntityRegistry)

    func detach()
}

/// Registry of mechanic types installed at app launch.
/// Add a new renderer file under `Mechanics/` and register it here.
public enum MechanicRegistry {
    public static let shipped: [MechanicKind: MechanicRenderer.Type] = [
        // TODO populate as you ship each:
        //   .annotatedCallouts : AnnotatedCalloutsRenderer.self,
        //   .highlightedRegion : HighlightedRegionRenderer.self,
        //   .explodedView      : ExplodedViewRenderer.self,
        //   .crossSection      : CrossSectionRenderer.self,
        //   .assemblySequence  : AssemblySequenceRenderer.self,
        //   .timeline          : TimelineRenderer.self,
        //   .ghostedInternal   : GhostedInternalRenderer.self,
    ]
}
#endif
