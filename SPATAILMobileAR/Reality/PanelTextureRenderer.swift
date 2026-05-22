// PanelTextureRenderer.swift
//
// Rasterizes a SpatialElement's source content into a UIImage suitable
// for use as a RealityKit unlit-material texture. The web viewer does
// the same trick with HTMLCanvas; this is the iPhone equivalent.
//
// Pixel density is 480 px / metre — readable when held at arm's length.

import Foundation
import UIKit
import RealityKit

enum PanelStyle {
    case standard       // two_d_panel
    case wallDashboard  // wall_dashboard
    case decision       // floating_decision_card
    case callout        // anchored_callout
    case diagnostic     // diagnostic_overlay
}

struct PanelTextureRenderer {
    /// Renders `element` into a TextureResource sized to `widthMeters x heightMeters`.
    /// Returns nil only if texture generation fails outright.
    func renderTexture(for element: SpatialElement,
                       widthMeters: Float, heightMeters: Float,
                       style: PanelStyle) -> TextureResource? {
        guard let image = renderImage(for: element,
                                      widthMeters: widthMeters,
                                      heightMeters: heightMeters,
                                      style: style),
              let cg = image.cgImage else { return nil }
        do {
            // SPATAIL_NEEDS_MAC_BUILD_VERIFY: TextureResource.generate(from:withName:options:)
            // is the sync iOS 13+ API. iOS 18 added an async variant — both
            // overloads coexist. If Xcode flags ambiguity, prefix with
            // `try TextureResource.generate(from: cg, withName: element.id, options: ...)`
            // explicitly typed via `as TextureResource`.
            return try TextureResource.generate(
                from: cg,
                withName: element.id,
                options: .init(semantic: .color),
            )
        } catch {
            print("[PanelTextureRenderer] texture generate failed for \(element.id): \(error)")
            return nil
        }
    }

    func renderImage(for element: SpatialElement,
                     widthMeters: Float, heightMeters: Float,
                     style: PanelStyle) -> UIImage? {
        let pxPerMeter: CGFloat = 480
        let size = CGSize(
            width: CGFloat(widthMeters) * pxPerMeter,
            height: CGFloat(heightMeters) * pxPerMeter,
        )
        let renderer = UIGraphicsImageRenderer(size: size)
        return renderer.image { ctx in
            drawPanel(into: ctx.cgContext, element: element, size: size, style: style)
        }
    }

    // MARK: - Drawing

    private func drawPanel(into ctx: CGContext, element: SpatialElement,
                           size: CGSize, style: PanelStyle) {
        // Background card.
        let radius: CGFloat = 28
        let bg = UIBezierPath(roundedRect: CGRect(origin: .zero, size: size), cornerRadius: radius)
        UIColor(red: 0.08, green: 0.094, blue: 0.125, alpha: 0.96).setFill()
        bg.fill()

        UIColor(red: 0.43, green: 0.66, blue: 1.0, alpha: 0.35).setStroke()
        bg.lineWidth = 4
        bg.stroke()

        // Accent stripe.
        let accent = accentColor(for: style)
        accent.setFill()
        UIBezierPath(rect: CGRect(x: 0, y: 0, width: 10, height: size.height)).fill()

        // Header.
        let pad: CGFloat = 28
        let titleFont = UIFont.systemFont(ofSize: size.height * 0.075, weight: .bold)
        let subFont = UIFont.monospacedSystemFont(ofSize: size.height * 0.04, weight: .medium)
        let titleAttrs: [NSAttributedString.Key: Any] = [
            .font: titleFont, .foregroundColor: UIColor.white,
        ]
        let subAttrs: [NSAttributedString.Key: Any] = [
            .font: subFont, .foregroundColor: UIColor(white: 0.6, alpha: 1.0),
        ]
        let title = element.title as NSString
        let titleRect = CGRect(x: pad, y: 22, width: size.width - 2 * pad,
                               height: size.height * 0.18)
        title.draw(in: titleRect, withAttributes: titleAttrs)

        let mode = element.representationMode.uppercased() as NSString
        mode.draw(at: CGPoint(x: pad, y: 22 + titleFont.lineHeight + 4),
                  withAttributes: subAttrs)

        // Content.
        let contentTop = 22 + titleFont.lineHeight + subFont.lineHeight + 28
        let contentRect = CGRect(
            x: pad, y: contentTop,
            width: size.width - 2 * pad,
            height: size.height - contentTop - 22,
        )
        drawContent(element: element, in: contentRect)
    }

    private func drawContent(element: SpatialElement, in rect: CGRect) {
        guard let sc = element.sourceContent else { return }

        let bodyFontSize = max(16, rect.height * 0.08)
        let bodyFont = UIFont.systemFont(ofSize: bodyFontSize, weight: .medium)
        let dimFont  = UIFont.systemFont(ofSize: bodyFontSize * 0.85, weight: .medium)
        let monoFont = UIFont.monospacedSystemFont(ofSize: bodyFontSize, weight: .semibold)
        let bigFont  = UIFont.systemFont(ofSize: bodyFontSize * 1.35, weight: .bold)

        let white = UIColor.white
        let dim   = UIColor(white: 0.62, alpha: 1.0)

        if let kpis = sc.kpis {
            drawKPIGrid(kpis: kpis, in: rect,
                        labelFont: dimFont, valueFont: bigFont, deltaFont: dimFont)
            return
        }
        if let facts = sc.facts {
            drawFacts(facts: facts, in: rect,
                      keyFont: dimFont, valueFont: bodyFont, keyColor: dim, valueColor: white)
            return
        }
        if let items = sc.items {
            drawBulletList(items: items, in: rect,
                           bulletColor: UIColor(red: 0.43, green: 0.66, blue: 1.0, alpha: 1.0),
                           textFont: bodyFont, textColor: white)
            return
        }
        if let steps = sc.steps {
            drawSteps(steps: steps, in: rect,
                      numFont: monoFont, textFont: bodyFont,
                      numColor: UIColor(red: 0.71, green: 0.42, blue: 1.0, alpha: 1.0),
                      textColor: white)
            return
        }
        if let options = sc.options {
            drawDecisions(options: options, in: rect,
                          labelFont: bodyFont, detailFont: dimFont,
                          labelColor: UIColor(red: 0.43, green: 0.66, blue: 1.0, alpha: 1.0),
                          detailColor: dim)
            return
        }
        if let body = sc.body {
            drawWrappedText(body, in: rect, font: bodyFont, color: white)
            return
        }
        if let finding = sc.finding {
            drawWrappedText(finding, in: rect, font: bodyFont, color: white)
            return
        }
        if let name = sc.name {
            drawWrappedText(name, in: rect, font: bodyFont, color: white)
            return
        }
    }

    private func drawKPIGrid(kpis: [KPIEntry], in rect: CGRect,
                             labelFont: UIFont, valueFont: UIFont, deltaFont: UIFont) {
        let cols = 2
        let rows = (kpis.count + cols - 1) / cols
        let cellW = rect.width / CGFloat(cols)
        let cellH = min(rect.height / CGFloat(rows), 200)
        for (i, k) in kpis.enumerated() {
            let cx = rect.minX + CGFloat(i % cols) * cellW
            let cy = rect.minY + CGFloat(i / cols) * cellH
            (k.label as NSString).draw(
                at: CGPoint(x: cx, y: cy),
                withAttributes: [.font: labelFont,
                                 .foregroundColor: UIColor(white: 0.6, alpha: 1.0)])
            (k.value as NSString).draw(
                at: CGPoint(x: cx, y: cy + labelFont.lineHeight + 2),
                withAttributes: [.font: valueFont, .foregroundColor: UIColor.white])
            if let delta = k.delta {
                let color: UIColor = (k.trend == "down")
                    ? UIColor(red: 0.94, green: 0.41, blue: 0.41, alpha: 1.0)
                    : UIColor(red: 0.96, green: 0.73, blue: 0.26, alpha: 1.0)
                (delta as NSString).draw(
                    at: CGPoint(x: cx, y: cy + labelFont.lineHeight + valueFont.lineHeight + 6),
                    withAttributes: [.font: deltaFont, .foregroundColor: color])
            }
        }
    }

    private func drawFacts(facts: [FactEntry], in rect: CGRect,
                           keyFont: UIFont, valueFont: UIFont,
                           keyColor: UIColor, valueColor: UIColor) {
        var y = rect.minY
        for f in facts {
            (f.key as NSString).draw(
                at: CGPoint(x: rect.minX, y: y),
                withAttributes: [.font: keyFont, .foregroundColor: keyColor])
            y += keyFont.lineHeight + 1
            y = drawWrappedText(f.value, in: CGRect(
                x: rect.minX, y: y, width: rect.width, height: rect.maxY - y,
            ), font: valueFont, color: valueColor)
            y += valueFont.lineHeight * 0.4
            if y >= rect.maxY { break }
        }
    }

    private func drawBulletList(items: [String], in rect: CGRect,
                                bulletColor: UIColor, textFont: UIFont, textColor: UIColor) {
        var y = rect.minY
        let bullet = "•" as NSString
        let bulletAttrs: [NSAttributedString.Key: Any] = [
            .font: textFont, .foregroundColor: bulletColor,
        ]
        for item in items {
            bullet.draw(at: CGPoint(x: rect.minX, y: y), withAttributes: bulletAttrs)
            y = drawWrappedText(item, in: CGRect(
                x: rect.minX + textFont.pointSize, y: y, width: rect.width - textFont.pointSize, height: rect.maxY - y,
            ), font: textFont, color: textColor)
            y += textFont.lineHeight * 0.3
            if y >= rect.maxY { break }
        }
    }

    private func drawSteps(steps: [String], in rect: CGRect,
                           numFont: UIFont, textFont: UIFont,
                           numColor: UIColor, textColor: UIColor) {
        var y = rect.minY
        for (i, step) in steps.enumerated() {
            let n = "\(i + 1)." as NSString
            n.draw(at: CGPoint(x: rect.minX, y: y),
                   withAttributes: [.font: numFont, .foregroundColor: numColor])
            y = drawWrappedText(step, in: CGRect(
                x: rect.minX + numFont.pointSize * 1.8, y: y,
                width: rect.width - numFont.pointSize * 1.8, height: rect.maxY - y,
            ), font: textFont, color: textColor)
            y += textFont.lineHeight * 0.4
            if y >= rect.maxY { break }
        }
    }

    private func drawDecisions(options: [DecisionOption], in rect: CGRect,
                               labelFont: UIFont, detailFont: UIFont,
                               labelColor: UIColor, detailColor: UIColor) {
        var y = rect.minY
        for opt in options {
            let label = "› \(opt.label)"
            y = drawWrappedText(label, in: CGRect(
                x: rect.minX, y: y, width: rect.width, height: rect.maxY - y,
            ), font: labelFont, color: labelColor)
            if let detail = opt.detail {
                y = drawWrappedText(detail, in: CGRect(
                    x: rect.minX + 12, y: y + 2, width: rect.width - 12, height: rect.maxY - y,
                ), font: detailFont, color: detailColor)
            }
            y += labelFont.lineHeight * 0.5
            if y >= rect.maxY { break }
        }
    }

    @discardableResult
    private func drawWrappedText(_ text: String, in rect: CGRect,
                                 font: UIFont, color: UIColor) -> CGFloat {
        let para = NSMutableParagraphStyle()
        para.lineBreakMode = .byWordWrapping
        let attrs: [NSAttributedString.Key: Any] = [
            .font: font, .foregroundColor: color, .paragraphStyle: para,
        ]
        let attr = NSAttributedString(string: text, attributes: attrs)
        attr.draw(with: rect, options: [.usesLineFragmentOrigin, .usesFontLeading],
                  context: nil)
        let h = attr.boundingRect(
            with: CGSize(width: rect.width, height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            context: nil,
        ).height
        return rect.minY + h
    }

    private func accentColor(for style: PanelStyle) -> UIColor {
        switch style {
        case .standard, .wallDashboard:
            return UIColor(red: 0.43, green: 0.66, blue: 1.0, alpha: 1.0)
        case .decision:
            return UIColor(red: 0.71, green: 0.42, blue: 1.0, alpha: 1.0)
        case .callout:
            return UIColor(red: 0.96, green: 0.73, blue: 0.26, alpha: 1.0)
        case .diagnostic:
            return UIColor(red: 0.94, green: 0.41, blue: 0.41, alpha: 1.0)
        }
    }
}
