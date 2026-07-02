import Foundation
import simd

// LayoutSolver — resolves the brain's placement INTENT (stage.layout + per-asset
// footprints) into local positions on the placed anchor. Swift twin of the web
// viewer's resolveLayout(): the contract carries intent, not coordinates, and this
// stands in for the on-device PlacementSolver for the viewer's purposes.

enum LayoutSolver {
    struct Slot { let id: String; let local: SIMD3<Float>; let footprint: SIMD3<Float> }

    static func footprint(_ a: ModularExperience.Asset) -> SIMD3<Float> {
        let d = (a.realSizeMeters?.count == 3) ? a.realSizeMeters! : a.scaleMeters
        return SIMD3(Float(d.count > 0 ? d[0] : 0.2),
                     Float(d.count > 1 ? d[1] : 0.2),
                     Float(d.count > 2 ? d[2] : 0.2))
    }

    /// Positions are LOCAL to the anchor root (anchor sits on the chosen surface, so
    /// y = footprint.height/2 grounds each asset). Mirrors arc/row/cluster/stack.
    static func solve(_ exp: ModularExperience) -> [Slot] {
        let assets = exp.assets
        guard !assets.isEmpty else { return [] }
        let layout = exp.stage.layout
        let fps = assets.map { footprint($0) }
        let widths = fps.map { max(0.06, $0.x) }
        let gap: Float = 0.12
        let totalW = widths.reduce(0, +) + gap * Float(max(assets.count - 1, 0))
        let cols = Int(ceil(sqrt(Double(assets.count))))
        var x = -totalW / 2, stackY: Float = 0
        var out: [Slot] = []
        for (i, a) in assets.enumerated() {
            let w = widths[i], h = max(0.06, fps[i].y)
            let cx = x + w / 2; x += w + gap
            var pos: SIMD3<Float>
            switch layout {
            case "arc" where assets.count > 1:
                let ang = (Float(i) / Float(assets.count - 1) - 0.5) * 0.7
                let R = 0.42 + totalW * 0.2
                pos = SIMD3(sin(ang) * R, h / 2, R - cos(ang) * R - R * 0.4)
            case "stack":
                pos = SIMD3(0, stackY + h / 2, 0); stackY += h + 0.02
            case "cluster", "grid":
                let r = i / cols, col = i % cols, sp: Float = 0.26
                pos = SIMD3((Float(col) - Float(cols - 1) / 2) * sp, h / 2, (Float(r) - 0.5) * sp)
            default: // row
                pos = SIMD3(cx, h / 2, 0)
            }
            out.append(Slot(id: a.id, local: pos, footprint: fps[i]))
        }
        return out
    }

    /// Affordances for the on-object control panel — from the design-system
    /// semanticActions (isolate_part->isolate, highlight_part->label), with rotate/scale.
    static func affordances(_ exp: ModularExperience, asset: ModularExperience.Asset) -> [String] {
        var out = exp.placement.semanticActions.map { a -> String in
            switch a { case "isolate_part": return "isolate"; case "highlight_part": return "label"; default: return a }
        }.filter { $0 != "reset" }
        if out.isEmpty { out = ["rotate", "scale", "isolate"] }
        if asset.supportsAnimation && !out.contains("animate") { out.append("animate") }
        return Array(Set(out))
    }

    /// Per-element intent: primary takes the experience intent; parts default to explain.
    static func intent(_ exp: ModularExperience, asset: ModularExperience.Asset, primaryId: String) -> String {
        asset.id == primaryId ? (exp.understanding.intent.isEmpty ? "inspect" : exp.understanding.intent) : "explain"
    }

    static func primaryId(_ exp: ModularExperience) -> String {
        exp.assets.first(where: { $0.role == "primary_object" })?.id ?? exp.assets.first?.id ?? ""
    }
}
