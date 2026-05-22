// AppRoot.swift
//
// Top-level navigation. The very first thing the app shows is the
// room scan — the whole product premise breaks if the planner places
// wall panels in fictional walls. Once a room is captured (or
// re-loaded from a previous session), we let the user into the demo
// selector.

import SwiftUI

struct AppRoot: View {
    @EnvironmentObject private var env: AppEnvironment
    @State private var hasRoom: Bool = RoomContractIO.mostRecent() != nil

    var body: some View {
        NavigationStack {
            if hasRoom {
                DemoSelectorView()
            } else {
                RoomScanView()
                    .onDisappear { hasRoom = RoomContractIO.mostRecent() != nil }
            }
        }
        .tint(.spatailAccent)
    }
}

// Brand palette — accent + dark surfaces — kept centralised so the
// whole app stays visually coherent without inline magic colors.
extension Color {
    static let spatailAccent  = Color(red: 0.43, green: 0.66, blue: 1.00)
    static let spatailAccent2 = Color(red: 0.71, green: 0.42, blue: 1.00)
    static let spatailBg      = Color(red: 0.05, green: 0.05, blue: 0.06)
    static let spatailBgElev  = Color(red: 0.08, green: 0.09, blue: 0.11)
    static let spatailBgPanel = Color(red: 0.10, green: 0.11, blue: 0.14)
    static let spatailBorder  = Color(red: 0.15, green: 0.16, blue: 0.20)
    static let spatailTextDim = Color(red: 0.55, green: 0.57, blue: 0.63)
}
