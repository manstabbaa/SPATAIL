// AskScope.swift — the "ask about THIS object" channel between the Lens and the AskBar.
//
// Tapping an object chip in the Lens pre-scopes the next ask to {objectId, part?}.
// RootView (W1) instantiates LensView and AskBar independently, so the scope rides
// a small shared observable both views watch — no AppModel change required.

import Foundation
import SwiftUI

@MainActor
final class AskScope: ObservableObject {

    static let shared = AskScope()

    struct Scope: Equatable {
        var objectId: UUID
        var label: String
        /// Part label ("cap") when the ask targets a sub-region of the object.
        var part: String?
    }

    @Published private(set) var scope: Scope?
    /// Bumped on every target() — the AskBar focuses its field when it changes.
    @Published private(set) var focusNonce = 0

    func target(objectId: UUID, label: String, part: String? = nil) {
        scope = Scope(objectId: objectId, label: label, part: part)
        focusNonce += 1
    }

    func clear() {
        scope = nil
    }
}
