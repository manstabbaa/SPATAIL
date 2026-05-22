// TabletopModelBuilder.swift
//
// Placeholder tabletop 3D model: a base slab with one colored stripe
// per component, plus a small floating title label. v1 — until we
// load real USDZ assets — this is what the user sees on the table.

import Foundation
import RealityKit
import UIKit

struct TabletopModelBuilder {
    func build(for element: SpatialElement) -> Entity {
        let group = Entity()
        let (w, h, d) = element.placement.boxSizeMeters

        // Base body
        let baseMaterial = SimpleMaterial(
            color: UIColor(red: 0.29, green: 0.33, blue: 0.44, alpha: 1.0),
            roughness: 0.55, isMetallic: false,
        )
        let base = ModelEntity(
            mesh: .generateBox(size: SIMD3<Float>(w, h, d), cornerRadius: 0.02),
            materials: [baseMaterial],
        )
        base.position.y = h / 2
        group.addChild(base)

        // Stripes per component — visual proxy for a real tabletop
        // model's parts.
        if let components = element.sourceContent?.components,
           !components.isEmpty {
            let stripeH = h * 0.18
            let palette: [UIColor] = [
                UIColor(red: 0.43, green: 0.66, blue: 1.0, alpha: 1.0),
                UIColor(red: 0.71, green: 0.42, blue: 1.0, alpha: 1.0),
                UIColor(red: 0.96, green: 0.73, blue: 0.26, alpha: 1.0),
                UIColor(red: 0.34, green: 0.82, blue: 0.61, alpha: 1.0),
                UIColor(red: 0.94, green: 0.41, blue: 0.41, alpha: 1.0),
            ]
            for (i, _) in components.prefix(5).enumerated() {
                let stripe = ModelEntity(
                    mesh: .generateBox(size: SIMD3<Float>(w * 1.005, stripeH, d * 1.005), cornerRadius: 0.01),
                    materials: [SimpleMaterial(color: palette[i % palette.count], roughness: 0.45, isMetallic: false)],
                )
                stripe.position.y = stripeH / 2 + Float(i) * stripeH * 1.05
                group.addChild(stripe)
            }
        }

        // Floating label above the model.
        let label = LabelEntity.make(
            text: element.title,
            widthMeters: max(w * 1.4, 0.6),
            heightMeters: 0.12,
        )
        label.position = SIMD3<Float>(0, h + 0.18, 0)
        group.addChild(label)
        return group
    }
}

// Shared 2D label entity — used by the tabletop title, exploded-view
// part labels, and floor-timeline plate labels. Cheap UIImage -> texture.
enum LabelEntity {
    static func make(text: String, widthMeters: Float, heightMeters: Float,
                     textColor: UIColor = .white,
                     bgColor: UIColor = UIColor(white: 0, alpha: 0.75)) -> Entity {
        let pxPerMeter: CGFloat = 480
        let size = CGSize(
            width: CGFloat(widthMeters) * pxPerMeter,
            height: CGFloat(heightMeters) * pxPerMeter,
        )
        let renderer = UIGraphicsImageRenderer(size: size)
        let image = renderer.image { _ in
            let path = UIBezierPath(roundedRect: CGRect(origin: .zero, size: size), cornerRadius: 16)
            bgColor.setFill(); path.fill()
            let attrs: [NSAttributedString.Key: Any] = [
                .font: UIFont.systemFont(ofSize: size.height * 0.5, weight: .semibold),
                .foregroundColor: textColor,
                .paragraphStyle: {
                    let p = NSMutableParagraphStyle()
                    p.alignment = .center
                    p.lineBreakMode = .byTruncatingTail
                    return p
                }(),
            ]
            let attr = NSAttributedString(string: text, attributes: attrs)
            let textSize = attr.size()
            let rect = CGRect(
                x: 0,
                y: (size.height - textSize.height) / 2,
                width: size.width,
                height: textSize.height,
            )
            attr.draw(in: rect)
        }
        let mesh = MeshResource.generatePlane(width: widthMeters, height: heightMeters)
        var mat = UnlitMaterial()
        if let cg = image.cgImage,
           let tex = try? TextureResource.generate(from: cg, withName: nil,
                                                   options: .init(semantic: .color)) {
            mat.color = .init(tint: .white, texture: MaterialParameters.Texture(tex))
        }
        mat.blending = .transparent(opacity: .init(scale: 1.0))
        return ModelEntity(mesh: mesh, materials: [mat])
    }
}
