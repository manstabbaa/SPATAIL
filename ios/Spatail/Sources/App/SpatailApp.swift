import SwiftUI

// Spatail — the Lens client for the PC Live Brain (docs/xr/LIVE_BRAIN_SPEC.md).
//
// AppModel is the composition root; every service it builds is injected as its
// OWN EnvironmentObject so views observe exactly the object they depend on
// (nested ObservableObjects don't republish through a parent — injecting only
// AppModel would leave views blind to uplink/scanner/registry changes).

@main
struct SpatailApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .environmentObject(model.settings)
                .environmentObject(model.hub)
                .environmentObject(model.scanner)
                .environmentObject(model.registry)
                .environmentObject(model.uplink)
                .environmentObject(model.runtime)
        }
    }
}
