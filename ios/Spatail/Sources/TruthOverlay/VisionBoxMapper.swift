// VisionBoxMapper.swift — THE fix for the misaligned-overlay bug.
//
// The old clients (SpatailViewer, LiveVisionView) drew VLM detection boxes by
// multiplying normalized coordinates straight against the view size:
//     rect = (x·W, y·H, w·W, h·H)
// That is only correct when the camera image and the screen share an aspect
// ratio — they never do. ARView renders the camera aspect-FILL, so the image is
// scaled and cropped; a box that was tight on the streamed frame landed tens of
// points off on screen.
//
// The correct mapping goes through ARFrame.displayTransform — the exact
// transform ARView itself uses to put the camera image on screen (rotation +
// aspect-fill scale + crop). Same recipe DepthSampler already proved, inverted.
//
// Coordinate spaces:
//   • VLM boxes are normalized [0–1], origin top-left, in the UPRIGHT image the
//     phone streamed. FrameStreamer orients the sensor buffer `.right`
//     (landscape sensor → portrait upright) before JPEG-encoding — the proven
//     legacy pipeline behavior (CameraFrameStreamer / EngineViewerView).
//   • displayTransform maps normalized SENSOR-image coordinates → normalized
//     view coordinates.
// So: upright → sensor (undo the .right rotation) → displayTransform → points.

import Foundation
import ARKit
import UIKit

@MainActor
enum VisionBoxMapper {

    /// Map a normalized detection box (upright streamed-image space, origin
    /// top-left) into view points, aspect-fill correct. Returns nil when the
    /// input is degenerate or the box lands entirely off screen.
    static func viewRect(uprightBox: CGRect,
                         frame: ARFrame,
                         viewportSize: CGSize,
                         orientation: UIInterfaceOrientation) -> CGRect? {
        guard viewportSize.width > 1, viewportSize.height > 1,
              uprightBox.width > 0, uprightBox.height > 0 else { return nil }

        let transform = frame.displayTransform(for: orientation, viewportSize: viewportSize)

        // Undo the `.right` orientation the streamer applied:
        // upright (u, v) → sensor (x, y) = (v, 1 − u), in normalized coords.
        func sensorPoint(_ p: CGPoint) -> CGPoint {
            CGPoint(x: p.y, y: 1 - p.x)
        }

        let corners = [
            CGPoint(x: uprightBox.minX, y: uprightBox.minY),
            CGPoint(x: uprightBox.maxX, y: uprightBox.minY),
            CGPoint(x: uprightBox.minX, y: uprightBox.maxY),
            CGPoint(x: uprightBox.maxX, y: uprightBox.maxY),
        ].map { sensorPoint($0).applying(transform) }

        guard let minX = corners.map(\.x).min(),
              let maxX = corners.map(\.x).max(),
              let minY = corners.map(\.y).min(),
              let maxY = corners.map(\.y).max() else { return nil }

        let rect = CGRect(x: minX * viewportSize.width,
                          y: minY * viewportSize.height,
                          width: (maxX - minX) * viewportSize.width,
                          height: (maxY - minY) * viewportSize.height)

        // Cull rects that fell wholly outside the screen (aspect-fill crop can
        // legitimately push part of the frame off screen — partial is fine).
        let screen = CGRect(origin: .zero, size: viewportSize)
        guard rect.intersects(screen), rect.width > 2, rect.height > 2 else { return nil }
        return rect
    }
}
