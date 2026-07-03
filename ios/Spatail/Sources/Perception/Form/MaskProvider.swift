// MaskProvider.swift — Perception v2 Form Engine, stage 1: mask-based sampling.
//
// VNGenerateForegroundInstanceMaskRequest (iOS 17+) runs ONCE per form pass on the
// captured image; each detection box then selects ITS instance(s) from the
// observation's low-res instance-label mask, producing a per-detection BINARY MASK
// at depth-map resolution (typically 256×192), ERODED 2 px — iPhone depth is sparse
// dToF upsampled with RGB guidance and bleeds at silhouettes, so the rim pixels are
// exactly the ones that lie.
//
// Coordinate spaces, explicitly:
//   • detection boxes: normalized, top-left, SENSOR space (the pipeline's canon);
//   • Vision runs with the same orientation as detection → the instance mask is in
//     UPRIGHT space; `uprightPoint(fromSensor:)` bridges per-pixel;
//   • the output BinaryMask indexes DEPTH-map pixels (sensor-oriented — the depth
//     map is aligned with the captured image).
//
// Runs on the Form Engine's serial background queue only. The degraded fallback
// (`boxMask`) needs no Vision at all — the old grid region as a mask.

import Foundation
import Vision
import CoreVideo
import CoreGraphics

final class MaskProvider {

    // MARK: Binary mask (depth-map resolution)

    struct BinaryMask {
        let width: Int
        let height: Int
        var bits: [Bool]
        /// Inclusive bounds of set bits (iteration window). Empty mask → minX > maxX.
        var minX: Int, minY: Int, maxX: Int, maxY: Int
        var setCount: Int

        @inline(__always) func isSet(_ x: Int, _ y: Int) -> Bool {
            x >= 0 && x < width && y >= 0 && y < height && bits[y * width + x]
        }

        var isEmpty: Bool { setCount == 0 }
    }

    /// Erosion radius in depth pixels (spec: 2–3 px; 2 at 256×192).
    static let erosionRadius = 2
    /// Box padding when intersecting the instance with the detection box —
    /// detector boxes are often a touch tight.
    static let boxPadFraction: CGFloat = 0.15
    /// The dominant instance must cover at least this fraction of the box.
    static let minInstanceBoxCoverage: Double = 0.02

    // MARK: Vision pass (once per form pass)

    /// Run the foreground instance segmentation. Heavy (~100–400 ms) — the caller
    /// measures latency and degrades to `boxMask` when the budget blows.
    func instanceObservation(pixelBuffer: CVPixelBuffer,
                             orientation: CGImagePropertyOrientation) throws
        -> VNInstanceMaskObservation? {
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer,
                                            orientation: orientation,
                                            options: [:])
        let request = VNGenerateForegroundInstanceMaskRequest()
        try handler.perform([request])
        return request.results?.first
    }

    // MARK: Per-detection mask

    /// The eroded binary mask (depth resolution) of the instance(s) under one
    /// sensor-space detection box. nil when no foreground instance overlaps the box.
    func mask(for sensorBox: CGRect,
              observation: VNInstanceMaskObservation,
              orientation: CGImagePropertyOrientation,
              depthWidth: Int, depthHeight: Int,
              erosion: Int = MaskProvider.erosionRadius) -> BinaryMask? {
        let labelMask = observation.instanceMask
        guard CVPixelBufferGetPixelFormatType(labelMask) == kCVPixelFormatType_OneComponent8
        else { return nil }
        let mw = CVPixelBufferGetWidth(labelMask)
        let mh = CVPixelBufferGetHeight(labelMask)
        guard mw > 0, mh > 0, depthWidth > 0, depthHeight > 0 else { return nil }

        CVPixelBufferLockBaseAddress(labelMask, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(labelMask, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(labelMask) else { return nil }
        let rowBytes = CVPixelBufferGetBytesPerRow(labelMask)
        let ptr = base.assumingMemoryBound(to: UInt8.self)

        @inline(__always) func label(atUpright ux: CGFloat, _ uy: CGFloat) -> UInt8 {
            let col = min(mw - 1, max(0, Int(ux * CGFloat(mw))))
            let row = min(mh - 1, max(0, Int(uy * CGFloat(mh))))
            return ptr[row * rowBytes + col]
        }

        // ── 1. Select the instance(s) under the box ──
        // Count label frequencies inside the UPRIGHT-space box, and totals over the
        // whole label mask (for the "mostly inside the box" secondary instances).
        let uprightBox = Self.uprightRect(fromSensor: sensorBox, orientation: orientation)
        var inBox = [Int](repeating: 0, count: 256)
        var total = [Int](repeating: 0, count: 256)
        var boxPixels = 0
        for row in 0..<mh {
            let v = ptr + row * rowBytes
            let uy = (CGFloat(row) + 0.5) / CGFloat(mh)
            let rowInBox = uy >= uprightBox.minY && uy <= uprightBox.maxY
            for col in 0..<mw {
                let value = Int(v[col])
                if value != 0 { total[value] += 1 }
                if rowInBox {
                    let ux = (CGFloat(col) + 0.5) / CGFloat(mw)
                    if ux >= uprightBox.minX && ux <= uprightBox.maxX {
                        boxPixels += 1
                        if value != 0 { inBox[value] += 1 }
                    }
                }
            }
        }
        guard boxPixels > 0 else { return nil }
        var dominant = 0
        for l in 1..<256 where inBox[l] > inBox[dominant] { dominant = l }
        guard dominant != 0,
              Double(inBox[dominant]) / Double(boxPixels) >= Self.minInstanceBoxCoverage
        else { return nil }
        var selected = Set<UInt8>([UInt8(dominant)])
        for l in 1..<256 where l != dominant && total[l] > 0 {
            if Double(inBox[l]) / Double(total[l]) >= 0.5 { selected.insert(UInt8(l)) }
        }

        // ── 2. Rasterize at DEPTH resolution, clipped to the padded sensor box ──
        let padded = sensorBox.insetBy(dx: -sensorBox.width * Self.boxPadFraction,
                                       dy: -sensorBox.height * Self.boxPadFraction)
            .intersection(CGRect(x: 0, y: 0, width: 1, height: 1))
        guard !padded.isNull else { return nil }

        var mask = BinaryMask(width: depthWidth, height: depthHeight,
                              bits: [Bool](repeating: false, count: depthWidth * depthHeight),
                              minX: depthWidth, minY: depthHeight, maxX: -1, maxY: -1,
                              setCount: 0)
        let x0 = max(Int(padded.minX * CGFloat(depthWidth)), 0)
        let x1 = min(Int(padded.maxX * CGFloat(depthWidth)), depthWidth - 1)
        let y0 = max(Int(padded.minY * CGFloat(depthHeight)), 0)
        let y1 = min(Int(padded.maxY * CGFloat(depthHeight)), depthHeight - 1)
        guard x0 <= x1, y0 <= y1 else { return nil }

        for dy in y0...y1 {
            let ny = (CGFloat(dy) + 0.5) / CGFloat(depthHeight)
            for dx in x0...x1 {
                let nx = (CGFloat(dx) + 0.5) / CGFloat(depthWidth)
                let up = Self.uprightPoint(fromSensor: CGPoint(x: nx, y: ny),
                                           orientation: orientation)
                guard selected.contains(label(atUpright: up.x, up.y)) else { continue }
                mask.bits[dy * depthWidth + dx] = true
                mask.setCount += 1
                mask.minX = min(mask.minX, dx); mask.maxX = max(mask.maxX, dx)
                mask.minY = min(mask.minY, dy); mask.maxY = max(mask.maxY, dy)
            }
        }
        guard !mask.isEmpty else { return nil }

        // ── 3. Erode (silhouette bleed) ──
        Self.erode(&mask, radius: erosion)
        return mask.isEmpty ? nil : mask
    }

    /// Degraded fallback (no Vision): every depth pixel inside the detection box —
    /// the old grid path's region, upgraded downstream by the confidence filter.
    static func boxMask(for sensorBox: CGRect,
                        depthWidth: Int, depthHeight: Int) -> BinaryMask? {
        let clipped = sensorBox.intersection(CGRect(x: 0, y: 0, width: 1, height: 1))
        guard !clipped.isNull, depthWidth > 0, depthHeight > 0 else { return nil }
        var mask = BinaryMask(width: depthWidth, height: depthHeight,
                              bits: [Bool](repeating: false, count: depthWidth * depthHeight),
                              minX: depthWidth, minY: depthHeight, maxX: -1, maxY: -1,
                              setCount: 0)
        let x0 = max(Int(clipped.minX * CGFloat(depthWidth)), 0)
        let x1 = min(Int(clipped.maxX * CGFloat(depthWidth)), depthWidth - 1)
        let y0 = max(Int(clipped.minY * CGFloat(depthHeight)), 0)
        let y1 = min(Int(clipped.maxY * CGFloat(depthHeight)), depthHeight - 1)
        guard x0 <= x1, y0 <= y1 else { return nil }
        for dy in y0...y1 {
            for dx in x0...x1 {
                mask.bits[dy * depthWidth + dx] = true
            }
        }
        mask.setCount = (x1 - x0 + 1) * (y1 - y0 + 1)
        mask.minX = x0; mask.maxX = x1; mask.minY = y0; mask.maxY = y1
        return mask
    }

    // MARK: Binary erosion (separable min-filter, radius px)

    static func erode(_ mask: inout BinaryMask, radius: Int) {
        guard radius > 0, !mask.isEmpty else { return }
        let w = mask.width, h = mask.height
        let x0 = max(mask.minX - 1, 0), x1 = min(mask.maxX + 1, w - 1)
        let y0 = max(mask.minY - 1, 0), y1 = min(mask.maxY + 1, h - 1)

        // Horizontal pass: survive only if all bits within ±radius are set.
        var pass = [Bool](repeating: false, count: w * h)
        for y in y0...y1 {
            let row = y * w
            for x in x0...x1 where mask.bits[row + x] {
                var keep = true
                for dx in -radius...radius {
                    let xx = x + dx
                    if xx < 0 || xx >= w || !mask.bits[row + xx] { keep = false; break }
                }
                pass[row + x] = keep
            }
        }
        // Vertical pass over the horizontal result.
        var out = [Bool](repeating: false, count: w * h)
        var setCount = 0
        var minX = w, minY = h, maxX = -1, maxY = -1
        for y in y0...y1 {
            for x in x0...x1 where pass[y * w + x] {
                var keep = true
                for dy in -radius...radius {
                    let yy = y + dy
                    if yy < 0 || yy >= h || !pass[yy * w + x] { keep = false; break }
                }
                if keep {
                    out[y * w + x] = true
                    setCount += 1
                    minX = min(minX, x); maxX = max(maxX, x)
                    minY = min(minY, y); maxY = max(maxY, y)
                }
            }
        }
        mask.bits = out
        mask.setCount = setCount
        mask.minX = minX; mask.maxX = maxX; mask.minY = minY; mask.maxY = maxY
    }

    // MARK: Sensor ↔ upright point/rect mapping
    //
    // Inverse direction of SensorSpace.rect(fromUpright:) — see DetectionService.
    // Orientation o makes the sensor image upright; these map sensor coords INTO
    // that upright space (normalized, top-left origin everywhere).

    static func uprightPoint(fromSensor p: CGPoint,
                             orientation: CGImagePropertyOrientation) -> CGPoint {
        switch orientation {
        case .right: return CGPoint(x: 1 - p.y, y: p.x)       // sensor = (uy, 1-ux)
        case .left:  return CGPoint(x: p.y, y: 1 - p.x)       // sensor = (1-uy, ux)
        case .down:  return CGPoint(x: 1 - p.x, y: 1 - p.y)
        default:     return p
        }
    }

    static func uprightRect(fromSensor r: CGRect,
                            orientation: CGImagePropertyOrientation) -> CGRect {
        switch orientation {
        case .right:
            return CGRect(x: 1 - r.maxY, y: r.minX, width: r.height, height: r.width)
        case .left:
            return CGRect(x: r.minY, y: 1 - r.maxX, width: r.height, height: r.width)
        case .down:
            return CGRect(x: 1 - r.maxX, y: 1 - r.maxY, width: r.width, height: r.height)
        default:
            return r
        }
    }

    /// Upright → sensor point (the forward maps SensorSpace documents).
    static func sensorPoint(fromUpright p: CGPoint,
                            orientation: CGImagePropertyOrientation) -> CGPoint {
        switch orientation {
        case .right: return CGPoint(x: p.y, y: 1 - p.x)       // sensor = (uy, 1-ux)
        case .left:  return CGPoint(x: 1 - p.y, y: p.x)       // sensor = (1-uy, ux)
        case .down:  return CGPoint(x: 1 - p.x, y: 1 - p.y)
        default:     return p
        }
    }
}
