import SwiftUI
import CoreGraphics

// Shared UI state for the SPATAIL iOS Viewer. The SwiftUI HUD observes it; the AR
// coordinator writes identify/detection state into it and reads the toggle/command
// closures it sets in makeUIView (the standard UIViewRepresentable bridge).

struct IdentifiedItem: Identifiable {
    let id: String
    var title: String
    var intent: String
    var color: Color
    var rect: CGRect            // in ARView point coordinates
    var affordances: [String]
    var why: String
    var placeholder: Bool
}

struct GenProgress {
    var stage: String
    var index: Int
    var total: Int
    var percent: Int
}

@MainActor
final class ViewerModel: ObservableObject {
    // server config
    @Published var brainURL = ViewerConfig.brainURL
    @Published var detectorURL = ViewerConfig.detectorURL

    // scene
    @Published var prompt = ""
    @Published var title = ""
    @Published var subtitle = ""
    @Published var status = "Set your PC address in Settings, aim at a surface, then Generate."

    // overlays
    @Published var identifyOn = false
    @Published var controlsOn = false
    @Published var liveOn = false
    @Published var identified: [IdentifiedItem] = []
    @Published var detections: [Detection] = []
    @Published var selectedId: String? = nil

    // generation
    @Published var generating = false
    @Published var progress: GenProgress? = nil

    // commands the AR coordinator installs (UIViewRepresentable bridge)
    var onGenerate: ((String) -> Void)?
    var onBehavior: ((String, String) -> Void)?      // (assetId, verb)
    var onSelect: ((String) -> Void)?
    var onReplaceAnchor: (() -> Void)?               // re-place the scene where you aim

    func intentColor(_ intent: String) -> Color {
        switch intent.lowercased() {
        case "explain", "inspect": return Color(red: 0.36, green: 0.29, blue: 0.90)
        case "compare": return Color(red: 0.10, green: 0.76, blue: 0.65)
        case "simulate": return Color(red: 0.90, green: 0.64, blue: 0.24)
        case "guide": return Color(red: 0.29, green: 0.61, blue: 0.90)
        case "recognize": return Color(red: 1.0, green: 0.36, blue: 0.48)
        default: return Color(white: 0.6)
        }
    }
}
