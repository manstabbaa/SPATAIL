// AskPlanner.swift — question-driven perception, the PURE half (v3 §7).
//
// The flow inversion the founder asked for: instead of describing everything
// at 1 Hz and hoping the answer is lying around, the QUESTION decides what to
// look at and how hard. This file turns a prompt into an EvidenceSpec — which
// dossier fields the question needs, whether it needs placement-grade
// geometry, whether it wants a spoken answer — and judges whether the entity's
// dossier already answers (fresh) so no perception runs at all.
//
// Foundation only — runs in the off-device harness. AppModel owns the impure
// half: resolve target → (dossier fresh? skip) → focus pass with the question
// riding along (bounded wait) → brain ask against the enriched registry.

import Foundation

enum AskPlanner {

    /// What a question needs from perception before the brain can do it justice.
    struct EvidenceSpec: Equatable {
        /// Dossier fields the prompt asks about (subset of the wire's wanted set).
        var wantedAttributes: [String]
        /// The ask targets edges/centers/fit — worth a focused geometry pass.
        var needsGeometry: Bool
        /// Reads as a question (wants an `answer` string back, not just fields).
        var isQuestion: Bool

        var wantsEvidence: Bool { !wantedAttributes.isEmpty || isQuestion }
    }

    /// Dossier freshness bar: attributes older than this are re-asked (v3 §7).
    static let dossierFreshSeconds: TimeInterval = 60
    /// How long the ask flow waits on a focus pass before proceeding anyway.
    static let focusWaitSeconds: TimeInterval = 3.5

    // Keyword tables — deliberately word-token exact (no substring surprises).
    private static let colorWords: Set<String> = ["color", "colors", "colour",
                                                  "colours", "colored", "coloured"]
    private static let textWords: Set<String> = ["say", "says", "written", "text",
                                                 "letters", "read", "reads",
                                                 "words", "label", "labeled",
                                                 "printed", "engraved"]
    private static let languageWords: Set<String> = ["language", "english",
                                                     "arabic", "french", "spanish",
                                                     "german", "japanese", "chinese",
                                                     "korean"]
    /// "make"/"model" stay OUT: they are everyday prompt verbs/nouns ("make me
    /// a…", "3d model") and would drag brand into almost every ask.
    private static let brandWords: Set<String> = ["brand", "company", "logo",
                                                  "manufacturer"]
    private static let materialWords: Set<String> = ["material", "materials",
                                                     "made", "wood", "wooden",
                                                     "metal", "plastic", "leather",
                                                     "fabric", "glass"]
    private static let stateWords: Set<String> = ["turned", "switched", "open",
                                                  "closed", "empty", "full",
                                                  "plugged", "charging"]
    private static let geometryWords: Set<String> = ["edge", "corner", "center",
                                                     "centre", "middle", "fit",
                                                     "fits", "between", "beside",
                                                     "armrest", "seat"]
    private static let questionStarters: Set<String> = ["what", "which", "is",
                                                        "are", "does", "do", "how",
                                                        "who", "where", "can",
                                                        "whats", "tell"]

    static func evidenceSpec(for prompt: String) -> EvidenceSpec {
        let lowered = prompt.lowercased()
        let tokens = Set(lowered.split(whereSeparator: { !$0.isLetter })
            .map(String.init))

        var wanted: [String] = []
        if !tokens.isDisjoint(with: colorWords) { wanted.append("colors") }
        if !tokens.isDisjoint(with: textWords) { wanted.append("textContent") }
        if !tokens.isDisjoint(with: languageWords) {
            wanted.append("language")
            if !wanted.contains("textContent") { wanted.append("textContent") }
        }
        if !tokens.isDisjoint(with: brandWords) { wanted.append("brand") }
        if !tokens.isDisjoint(with: materialWords) { wanted.append("materials") }
        if !tokens.isDisjoint(with: stateWords) { wanted.append("state") }

        let needsGeometry = !tokens.isDisjoint(with: geometryWords)
        let firstToken = lowered.split(whereSeparator: { !$0.isLetter })
            .first.map(String.init)
        let isQuestion = prompt.contains("?")
            || firstToken.map(questionStarters.contains) == true

        return EvidenceSpec(wantedAttributes: wanted,
                            needsGeometry: needsGeometry,
                            isQuestion: isQuestion)
    }

    /// Does the dossier already answer every wanted field, freshly?
    /// A question with NO named fields is satisfied by any fresh dossier.
    static func dossierAnswers(_ object: SpatailObject, wanted: [String],
                               now: TimeInterval) -> Bool {
        guard let attrs = object.attributes, !attrs.isEmpty,
              let at = object.attributesUpdatedAt,
              now - at < dossierFreshSeconds else { return false }
        for field in wanted {
            switch field {
            case "colors":      if attrs.colors?.isEmpty ?? true { return false }
            case "materials":   if attrs.materials?.isEmpty ?? true { return false }
            case "textContent": if attrs.textContent?.isEmpty ?? true { return false }
            case "language":    if attrs.language == nil { return false }
            case "brand":       if attrs.brand == nil { return false }
            case "state":       if attrs.state == nil { return false }
            default: break
            }
        }
        return true
    }

    /// One human line summarizing what the dossier knows — the instant answer
    /// path ("keyboard — blue keys, English") when perception can be skipped.
    static func dossierLine(label: String?, attributes: ObjectAttributes?) -> String? {
        guard let attrs = attributes, !attrs.isEmpty else { return nil }
        var bits: [String] = []
        if let colors = attrs.colors, !colors.isEmpty {
            bits.append(colors.prefix(3).joined(separator: "/"))
        }
        if let materials = attrs.materials, !materials.isEmpty {
            bits.append(materials.prefix(2).joined(separator: "/"))
        }
        if let text = attrs.textContent, !text.isEmpty {
            bits.append("“" + text.prefix(2).joined(separator: ", ") + "”")
        }
        if let language = attrs.language { bits.append(language) }
        if let brand = attrs.brand { bits.append(brand) }
        if let state = attrs.state { bits.append(state) }
        guard !bits.isEmpty else { return nil }
        let name = label ?? "object"
        return "\(name) — \(bits.joined(separator: " · "))"
    }
}
