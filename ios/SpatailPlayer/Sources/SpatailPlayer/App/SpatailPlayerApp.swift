// SpatailPlayerApp.swift
// @main entry. Single window. URL handler routes .spatail opens into the
// PlayerView.

#if canImport(SwiftUI)
import SwiftUI

@main
public struct SpatailPlayerApp: App {
    public init() {}

    public var body: some Scene {
        WindowGroup {
            PlayerView()
        }
    }
}
#endif
