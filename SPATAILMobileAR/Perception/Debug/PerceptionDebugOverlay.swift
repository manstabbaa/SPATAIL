// PerceptionDebugOverlay.swift
//
// The DEBUG MODE view. Draws everything needed to verify the pipeline by
// eye, on top of the AR feed:
//   • 2D bounding boxes (normalized → view space) with label + confidence
//   • per-detection anchor type, resolve method, world position
//   • a bottom HUD: detection source, intent, tracking quality, the
//     semantic scene context, and the placement DECISION + its reason
//
// Pure SwiftUI; reads a PerceptionDebugModel. No AR or RealityKit here.

import SwiftUI

struct PerceptionDebugOverlay: View {
    @ObservedObject var model: PerceptionDebugModel

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .topLeading) {
                // ── 2D bounding boxes over the camera feed ──
                ForEach(model.detections) { det in
                    boundingBox(for: det, in: geo.size)
                }
            }
            .frame(width: geo.size.width, height: geo.size.height)
        }
        .allowsHitTesting(false)
        .overlay(alignment: .bottom) { hud }
    }

    // MARK: - Bounding box

    private func boundingBox(for det: SpatailDetection, in size: CGSize) -> some View {
        let box = det.screenBoundingBox
        let rect = CGRect(x: box.x * size.width,
                          y: box.y * size.height,
                          width: box.width * size.width,
                          height: box.height * size.height)
        let color: Color = det.isResolved ? .spatailAccent : .orange
        return ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 6)
                .stroke(color, lineWidth: 2)
                .frame(width: rect.width, height: rect.height)
            VStack(alignment: .leading, spacing: 1) {
                Text("\(det.label)  \(Int(det.confidence * 100))%")
                    .font(.caption2).fontWeight(.bold)
                if let hit = det.worldHit {
                    Text("\(hit.anchorType.displayName) · \(hit.method.rawValue)")
                        .font(.system(size: 8))
                    Text(positionString(hit.worldPosition))
                        .font(.system(size: 8, design: .monospaced))
                } else {
                    Text("unresolved").font(.system(size: 8))
                }
            }
            .padding(3)
            .background(color.opacity(0.85))
            .foregroundColor(.black)
            .clipShape(RoundedRectangle(cornerRadius: 4))
            .offset(y: -2)
            .fixedSize()
        }
        .offset(x: rect.minX, y: rect.minY)
    }

    // MARK: - HUD

    private var hud: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                trackingBadge
                Label(model.sourceName, systemImage: "eye.circle")
                    .font(.caption).foregroundColor(.spatailAccent)
                Spacer()
                Text("frame \(model.frameId)")
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(.spatailTextDim)
            }

            Text("Intent: \(model.intent.isEmpty ? "—" : model.intent)")
                .font(.caption2).foregroundColor(.white)
                .lineLimit(2)

            // Scene context (the semantic RoomModel snapshot).
            HStack(spacing: 6) {
                contextChip("planes \(model.sceneContext.detectedPlaneCount)", on: model.sceneContext.detectedPlaneCount > 0)
                contextChip("floor", on: model.sceneContext.hasFloor)
                contextChip("table", on: model.sceneContext.hasTable)
                contextChip("wall", on: model.sceneContext.hasWall)
                contextChip("mesh", on: model.sceneContext.sceneMeshAvailable)
            }

            Divider().background(Color.spatailBorder)

            // The placement DECISION + why.
            Text("Decision: \(model.placementCount) placement(s) · \(model.activeAnchors) anchor(s)")
                .font(.caption2).fontWeight(.semibold).foregroundColor(.spatailAccent2)
            if let plan = model.lastPlan {
                ForEach(plan.placements.prefix(4)) { p in
                    HStack(alignment: .top, spacing: 4) {
                        Text("▸ \(p.asset.overlayKind.displayName)")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundColor(.white)
                        Text(p.reason.anchorRationale)
                            .font(.system(size: 9))
                            .foregroundColor(.spatailTextDim)
                            .lineLimit(2)
                    }
                }
                if plan.placements.isEmpty {
                    Text(plan.reasoningSummary)
                        .font(.system(size: 9)).foregroundColor(.spatailTextDim)
                }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.spatailBgElev.opacity(0.92))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.spatailBorder, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .padding(.horizontal, 10)
        .padding(.bottom, 96)   // sit above the control strip
    }

    private var trackingBadge: some View {
        let q = model.trackingQuality
        let color: Color = q == .normal ? .green : (q == .limited ? .yellow : .red)
        return HStack(spacing: 4) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(q.displayName).font(.caption2).foregroundColor(.white)
        }
    }

    private func contextChip(_ text: String, on: Bool) -> some View {
        Text(text)
            .font(.system(size: 9, weight: .medium))
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background((on ? Color.spatailAccent : Color.spatailBorder).opacity(on ? 0.25 : 0.5))
            .foregroundColor(on ? .spatailAccent : .spatailTextDim)
            .clipShape(Capsule())
    }

    private func positionString(_ p: SIMD3<Float>) -> String {
        String(format: "(%.2f, %.2f, %.2f)", p.x, p.y, p.z)
    }
}
