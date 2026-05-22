// SpatailPlayerApp.swift
// Optional Scene shell so a consuming app can do `WindowGroup { PlayerView() }`
// or just embed `PlayerView()` directly. Not `@main` — the consuming app target
// owns the process entry point.

#if os(iOS) || os(visionOS)
import SwiftUI

public struct SpatailPlayerApp: App {
    public init() {}

    public var body: some Scene {
        WindowGroup {
            PlayerView()
        }
    }
}
#endif
