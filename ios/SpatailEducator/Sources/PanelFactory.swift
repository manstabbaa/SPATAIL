import SwiftUI
#if os(iOS)
import RealityKit
import UIKit

// PanelFactory — turns a spec Panel (the "written" half) into a 3D entity: a
// readable card placed in space next to its hero. On iOS we render a SwiftUI card
// to a UIImage and map it onto an unlit plane (RealityView SwiftUI attachments are
// visionOS-first; this gives the same result on the phone). BillboardComponent
// (added by the runtime) keeps it facing the learner. Text stays flat — no depth,
// per Apple HIG (docs/apple-visionos/hig/spatial-layout: "avoid adding depth to text").

enum PanelFactory {
    /// Build a 3D panel entity for a spec Panel. Sized in metres; ~0.26 m wide card.
    @MainActor
    static func make(_ panel: ExperienceSpec.Panel) -> Entity {
        let widthM: Float = 0.26
        let img = render(panel: panel, pixelWidth: 512)
        let aspect = Float(img.size.height / max(img.size.width, 1))
        let heightM = widthM * aspect

        let mesh = MeshResource.generatePlane(width: widthM, height: heightM, cornerRadius: 0.012)
        var material = UnlitMaterial()
        if let cg = img.cgImage, let tex = makeTexture(cg) {
            material.color = .init(tint: .white, texture: .init(tex))
        } else {
            material.color = .init(tint: .white.withAlphaComponent(0.9))
        }
        material.blending = .transparent(opacity: 1.0)

        let entity = ModelEntity(mesh: mesh, materials: [material])
        entity.name = "panel:\(panel.id)"
        return entity
    }

    /// Build a TextureResource from a CGImage across iOS versions. The
    /// `TextureResource(image:options:)` initializer is iOS 18+; on iOS 17 fall
    /// back to the `generate(from:options:)` factory.
    private static func makeTexture(_ cg: CGImage) -> TextureResource? {
        if #available(iOS 18.0, *) {
            return try? TextureResource(image: cg, options: .init(semantic: .color))
        } else {
            return try? TextureResource.generate(from: cg, options: .init(semantic: .color))
        }
    }

    /// Render the panel card as a UIImage using SwiftUI.
    @MainActor
    private static func render(panel: ExperienceSpec.Panel, pixelWidth: CGFloat) -> UIImage {
        let card = PanelCard(panel: panel).frame(width: 260)
        let renderer = ImageRenderer(content: card)
        renderer.scale = pixelWidth / 260.0
        renderer.isOpaque = false
        return renderer.uiImage ?? UIImage()
    }
}

// The visual design of a panel card. Kept simple, legible, glassy.
private struct PanelCard: View {
    let panel: ExperienceSpec.Panel

    private var accent: Color {
        switch panel.kind {
        case "title":   return .blue
        case "data":    return .teal
        case "quiz":    return .orange
        case "caption": return .gray
        default:        return .indigo   // fact
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if !panel.title.isEmpty {
                Text(panel.title)
                    .font(.system(size: 15, weight: .bold, design: .rounded))
                    .foregroundStyle(accent)
            }
            if panel.kind == "quiz" {
                Text(panel.question.isEmpty ? panel.body : panel.question)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.primary)
                ForEach(Array(panel.options.enumerated()), id: \.offset) { _, opt in
                    HStack(spacing: 6) {
                        Image(systemName: "circle").font(.system(size: 9))
                        Text(opt).font(.system(size: 12))
                    }.foregroundStyle(.secondary)
                }
            } else if !panel.body.isEmpty {
                Text(panel.body)
                    .font(.system(size: 13))
                    .foregroundStyle(.primary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(accent.opacity(0.4), lineWidth: 1))
        .environment(\.colorScheme, .dark)
    }
}
#endif
