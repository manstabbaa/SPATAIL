// AskBar.swift — the prompt input at the thumb.
//
// Any prompt → AppModel.ask → PC brain /modular → diff-applied into the scene.
// When the Lens pre-scoped the ask to an object (AskScope), the bar shows the
// scope token ("bottle · cap") and appends a structured [object-context] block —
// the object's label, part, MEASURED size and registry objectId — so the brain's
// design system binds the experience to the real thing (anchoring.mode=object,
// PlacementTarget .object/.part on device).
//
// Voice input rides the system keyboard's dictation key — no custom speech stack.
// Generation progress for Meshy-built assets renders above the bar
// (GenerationProgressView, the 9-stage port from SpatailViewer).

import SwiftUI

struct AskBar: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var registry: ObjectRegistry
    @ObservedObject private var askScope = AskScope.shared
    @ObservedObject private var generation = GenerationTracker.shared

    @State private var text = ""
    @FocusState private var focused: Bool

    private var isGenerating: Bool {
        if case .generating = model.askState { return true }
        return false
    }

    var body: some View {
        VStack(spacing: SpatailSpace.s2) {
            if generation.progress != nil {
                GenerationProgressView()
            }
            if let scope = askScope.scope {
                scopeToken(scope)
            }
            inputRow
        }
        .onChange(of: askScope.focusNonce) { _, _ in
            focused = true
        }
    }

    // MARK: Scope token — the ask is aimed at a real object

    private func scopeToken(_ scope: AskScope.Scope) -> some View {
        HStack(spacing: SpatailSpace.s2) {
            Image(systemName: "scope")
                .imageScale(.small)
                .foregroundStyle(SpatailColor.indigo300)
            Text(scope.part.map { "\(scope.label) · \($0)" } ?? scope.label)
                .spatailType(.sm, weight: .medium)
                .foregroundStyle(SpatailColor.paper)
                .lineLimit(1)
            SpatailIconButton(systemName: "xmark", label: "Clear object scope",
                              size: .sm, variant: .ghost) {
                askScope.clear()
            }
        }
        .padding(.leading, SpatailSpace.s3)
        .padding(.trailing, SpatailSpace.s1)
        .padding(.vertical, SpatailSpace.s1)
        .spatailGlass(tone: .dark, radius: SpatailRadius.pill)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Input row

    private var inputRow: some View {
        HStack(spacing: SpatailSpace.s2) {
            TextField(placeholder, text: $text, axis: .vertical)
                .spatailType(.base)
                .foregroundStyle(SpatailColor.paper)
                .tint(SpatailColor.indigo300)
                .lineLimit(1...3)
                .focused($focused)
                .submitLabel(.go)
                .onSubmit(submit)
                .padding(.leading, SpatailSpace.s2)

            SpatailIconButton(systemName: isGenerating ? "hourglass" : "arrow.up",
                              label: "Ask",
                              variant: .solid,
                              disabled: sendDisabled) {
                submit()
            }
        }
        .padding(SpatailSpace.s2)
        .spatailGlass(tone: .dark, radius: SpatailRadius.glass)
    }

    private var placeholder: String {
        if let scope = askScope.scope {
            if let part = scope.part { return "Ask about the \(scope.label)'s \(part)…" }
            return "Ask about the \(scope.label)…"
        }
        return "Ask for an experience…"
    }

    private var sendDisabled: Bool {
        isGenerating || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    // MARK: Submit — prompt + object context → brain

    private func submit() {
        let raw = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty, !isGenerating else { return }
        focused = false
        text = ""
        model.ask(composedPrompt(raw))
    }

    /// Append the scoped object's identity + measured form so the brain targets
    /// the real thing. The scope survives the send — iterating on the same
    /// object is the common loop; the ✕ on the token releases it.
    private func composedPrompt(_ raw: String) -> String {
        guard let scope = askScope.scope else { return raw }

        var lines: [String] = []
        var target = "Target: the real \(scope.label) in front of the user"
        if let part = scope.part { target += " — specifically its \(part)" }
        lines.append(target + ".")

        // Merge-alias aware: a chip tapped pre-merge still describes the survivor.
        if let object = registry.object(for: scope.objectId) {
            let e = object.obb.extents
            lines.append(String(format: "Measured size: %.2f × %.2f × %.2f m.", e.x, e.y, e.z))
            var idLine = "objectId: \(object.id.uuidString)"
            if let part = scope.part { idLine += ", part: \(part)" }
            lines.append(idLine)
            if let surface = object.supportSurfaceId {
                lines.append("Support surface: \(surface).")
            }
        }
        var anchor = "Anchor the experience to this object"
        if let part = scope.part { anchor += "'s \(part)" }
        lines.append(anchor + " (anchoring.mode=object).")

        return raw + "\n\n[object-context]\n" + lines.joined(separator: "\n")
    }
}
