import Foundation

// Mirrors studio/out/<exhibit>_metadata.json — the SPATAIL handoff the studio
// emits. Decodable so the app reads exactly what Blender produced.

struct SpatailMetadata: Decodable {
    let sceneId: String
    let title: String
    let usdz: String?
    let glb: String
    let mode: String
    let animation: Animation
    let beats: [Beat]

    struct Animation: Decodable {
        let clip: String
        let fps: Int
        let frames: Int
        let seconds: Double
        let loop: Bool
    }

    struct Footprint: Decodable { let w: Double; let d: Double; let h: Double }

    struct Motion: Decodable {
        let module: String
        // module params are open-ended; keep the ones the runtime might use
        let span_m: Double?
        let distance_m: Double?
        let a_mps2: Double?
    }

    struct Beat: Decodable, Identifiable {
        let id: String
        let law: String?
        let subtitle: String?
        let title: String
        let narration: String?
        let demo: String
        let motion: [Motion]
        let footprint_m: Footprint
    }
}

// A demo entry the user can pick ("what do you want to see"). Backed by a USDZ
// the studio already built + its metadata.
struct CatalogEntry: Identifiable {
    let id: String          // beat id, e.g. "law2_fma"
    let title: String
    let subtitle: String
    let usdzName: String     // resource name in the bundle (without extension)
    let footprint: SpatailMetadata.Footprint
    let narration: String
}
