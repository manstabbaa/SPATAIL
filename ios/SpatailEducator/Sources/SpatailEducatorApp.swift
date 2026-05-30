import SwiftUI

// SPATAIL Educator — the phone/Vision Pro player.
//
// Pipeline position:  Blender → SPATAIL (USDZ + metadata) → THIS APP.
// The app is a thin, modular runtime: it does NOT model anything. It loads the
// USDZ the studio built, reads the SPATAIL metadata (motion modules, real
// footprint, narration), scans the room, runs SPATAIL ANALYSIS to choose a
// scale that fits, and plays the demo in AR.

@main
struct SpatailEducatorApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
