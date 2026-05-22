// PromptIntentClassifier.swift
//
// v1 stub. Maps a raw prompt to one of the bundled-demo intents so the
// app can route "help me service my mustang" → mustang demo and
// "explain Q3 costs" → q3 demo without a backend round-trip. Real
// intent classification (LLM-backed) lands later.

import Foundation

enum PromptIntent: Hashable {
    case mustangService
    case q3Review
    case unknown(String)
}

protocol PromptIntentClassifying {
    func classify(_ prompt: String) -> PromptIntent
}

final class PromptIntentClassifier: PromptIntentClassifying {
    func classify(_ prompt: String) -> PromptIntent {
        let p = prompt.lowercased()
        let hasMustang = p.contains("mustang") || (p.contains("service") && p.contains("car"))
        if hasMustang { return .mustangService }

        let q3Tokens = ["q3", "quarter", "manufacturing", "kpi", "cost", "factory"]
        if q3Tokens.filter({ p.contains($0) }).count >= 2 { return .q3Review }

        return .unknown(prompt)
    }
}
