// FloorTimelineBuilder.swift
//
// Floor-timeline element: a row of thin colored plates lying flat on
// the floor, each with a label. For Q3 the events read as "Jul wk 2",
// "Aug wk 5"… and become stepping stones the user can physically walk
// along (the Vision Pro design intent).

import Foundation
import RealityKit
import UIKit

struct FloorTimelineBuilder {
    func build(for element: SpatialElement) -> Entity {
        let group = Entity()

        // Pull events from sourceContent (timeline) or fall back to steps.
        let labels: [(label: String, sub: String?)] = {
            if let events = element.sourceContent?.events {
                return events.map { ($0.label, $0.when) }
            }
            if let steps = element.sourceContent?.steps {
                return steps.enumerated().map { (i, s) in ("Step \(i + 1)", s) }
            }
            return [("Step 1", nil), ("Step 2", nil), ("Step 3", nil)]
        }()

        let plateW: Float = 0.4
        let plateH: Float = 0.02
        let plateD: Float = 0.4
        let gap: Float = 0.12
        let totalLen = Float(labels.count) * (plateW + gap)

        let palette: [UIColor] = [
            UIColor(red: 0.43, green: 0.66, blue: 1.0, alpha: 1.0),
            UIColor(red: 0.71, green: 0.42, blue: 1.0, alpha: 1.0),
            UIColor(red: 0.96, green: 0.73, blue: 0.26, alpha: 1.0),
            UIColor(red: 0.34, green: 0.82, blue: 0.61, alpha: 1.0),
            UIColor(red: 0.94, green: 0.41, blue: 0.41, alpha: 1.0),
        ]

        let mesh = MeshResource.generateBox(
            size: SIMD3<Float>(plateW, plateH, plateD), cornerRadius: 0.02,
        )

        for (i, item) in labels.enumerated() {
            let mat = SimpleMaterial(
                color: palette[i % palette.count].withAlphaComponent(0.92),
                roughness: 0.5, isMetallic: false,
            )
            let plate = ModelEntity(mesh: mesh, materials: [mat])
            let x = -totalLen / 2 + Float(i) * (plateW + gap) + plateW / 2
            plate.position = SIMD3<Float>(x, plateH / 2, 0)
            group.addChild(plate)

            let title = LabelEntity.make(
                text: item.label,
                widthMeters: 0.36, heightMeters: 0.06,
            )
            title.position = SIMD3<Float>(x, plateH + 0.005, 0)
            title.orientation = simd_quatf(angle: -.pi / 2, axis: SIMD3<Float>(1, 0, 0))
            group.addChild(title)

            if let sub = item.sub {
                let subLabel = LabelEntity.make(
                    text: sub, widthMeters: 0.28, heightMeters: 0.04,
                    bgColor: UIColor(white: 0, alpha: 0.6),
                )
                subLabel.position = SIMD3<Float>(x, plateH + 0.005, 0.12)
                subLabel.orientation = simd_quatf(angle: -.pi / 2, axis: SIMD3<Float>(1, 0, 0))
                group.addChild(subLabel)
            }
        }

        // Connector: thin strip beneath the plates so the sequence
        // reads as a path, not isolated steps.
        if labels.count > 1 {
            let strip = ModelEntity(
                mesh: .generateBox(size: SIMD3<Float>(totalLen - plateW, 0.003, 0.04),
                                   cornerRadius: 0.002),
                materials: [SimpleMaterial(
                    color: UIColor(red: 0.43, green: 0.66, blue: 1.0, alpha: 0.5),
                    roughness: 0.3, isMetallic: false,
                )],
            )
            strip.position.y = 0.012
            group.addChild(strip)
        }

        return group
    }
}
