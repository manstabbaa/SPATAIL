// CorporateReviewSceneBuilder.swift
//
// Adds Q3-style scenery: a thin "boardroom table" slab beneath where
// the tabletop process model will appear. Pure scenery; the per-
// element renderers do all the content.

import Foundation
import RealityKit
import UIKit

struct CorporateReviewSceneBuilder {
    func addScenery(to root: Entity) {
        let topW: Float = 1.5
        let topH: Float = 0.04
        let topD: Float = 1.0
        let topMat = SimpleMaterial(
            color: UIColor(red: 0.35, green: 0.27, blue: 0.20, alpha: 1.0),
            roughness: 0.6, isMetallic: false,
        )
        let top = ModelEntity(
            mesh: .generateBox(size: SIMD3<Float>(topW, topH, topD), cornerRadius: 0.02),
            materials: [topMat],
        )
        top.position = SIMD3<Float>(0, 0.73, 0)
        top.name = "boardroom_table_top"
        root.addChild(top)

        // Four short legs for a hint of structure.
        let legMat = SimpleMaterial(
            color: UIColor(red: 0.20, green: 0.16, blue: 0.13, alpha: 1.0),
            roughness: 0.8, isMetallic: false,
        )
        let legOffsetsX: [Float] = [-0.65, 0.65]
        let legOffsetsZ: [Float] = [-0.4, 0.4]
        for x in legOffsetsX {
            for z in legOffsetsZ {
                let leg = ModelEntity(
                    mesh: .generateBox(size: SIMD3<Float>(0.05, 0.73, 0.05), cornerRadius: 0.005),
                    materials: [legMat],
                )
                leg.position = SIMD3<Float>(x, 0.365, z)
                root.addChild(leg)
            }
        }
    }
}
