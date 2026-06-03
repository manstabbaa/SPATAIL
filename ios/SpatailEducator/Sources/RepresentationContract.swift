import Foundation

// Swift mirrors of the SPATAIL Representation Engine's runtime payload — the
// JSON the server returns for a `mode:"representation"` job:
//   runtime_url      -> RuntimeSceneContract   (studio/runtime/scene_builder.py)
//   progressive_url  -> ProgressiveBundle       (studio/representation/job_entry.py)
//   delivery_url     -> DeliveryPackage          (studio/asset_factory/types.py)
//
// These are platform-neutral data (shared iOS + visionOS). Each struct decodes
// only the subset the runtime needs; unknown keys are ignored by Decodable.
// Field names match the Python emitters exactly (see CodingKeys for snake_case).
// Reuses JSONValue from ExperienceSpec.swift for open-ended interaction params.

struct RuntimeSceneContract: Decodable {
    let schemaVersion: String
    let sceneId: String
    let title: String
    let representation: Representation
    let placement: Placement
    let staging: Staging
    let beats: [Beat]
    let interactions: [Interaction]
    let assets: [Asset]

    struct Representation: Decodable {
        let domain: String
        let intent: String
        let strategy: String
        let subject: String
        var reasoning: String = ""
        enum CodingKeys: String, CodingKey { case domain, intent, strategy, subject, reasoning }
    }

    struct Placement: Decodable {
        let anchor: String                 // table | floor | free
        let layout: String                 // arc | line | cluster
        var facing: String = "user"
        var scaleMode: String = "dynamic"  // dynamic | fixed
    }

    struct Staging: Decodable {
        let distanceM: Double
        let spreadDeg: Double
        enum CodingKeys: String, CodingKey {
            case distanceM = "distance_m"
            case spreadDeg = "spread_deg"
        }
    }

    struct Footprint: Decodable {
        let w: Double                      // width (x), metres
        let h: Double                      // height (up), metres
        let d: Double                      // depth (z), metres
    }

    struct Beat: Decodable, Identifiable {
        let id: String
        let title: String
        let narration: String
        var focusTarget: [Double] = []     // eye-relative y-up; informational
        var distanceM: Double = 0
        var inComfortCone: Bool = true
        var reveal: String = "auto"        // auto | on_focus | on_tap
        enum CodingKeys: String, CodingKey {
            case id, title, narration, focusTarget, distanceM, inComfortCone, reveal
        }
    }

    struct Interaction: Decodable, Identifiable {
        let id: String
        let type: String                   // play_baked | tap_reveal | grab_physics | quiz_panel
        var label: String = ""
        var params: [String: JSONValue] = [:]
        var trigger: String = "on_tap"
    }

    struct Asset: Decodable, Identifiable {
        let id: String
        let role: String                   // primary_object | part | comparison_object | environment | label
        var status: String = "placeholder" // processed | placeholder
        let footprint: Footprint
        var positionYup: [Double] = []     // eye-relative y-up; AR placement uses StationLayout instead
        var yawRad: Double = 0
        var inComfortCone: Bool = true
        enum CodingKeys: String, CodingKey {
            case id, role, status, footprint
            case positionYup = "position_yup"
            case yawRad = "yaw_rad"
            case inComfortCone
        }
    }
}

// The progressive_url body: how to load — placeholder-first, then phased reveal.
struct ProgressiveBundle: Decodable {
    let loadPlan: LoadPlan
    let placeholderScene: PlaceholderScene

    struct LoadPlan: Decodable {
        let firstInteractiveMsBudget: Int
        let phases: [Phase]
        enum CodingKeys: String, CodingKey {
            case firstInteractiveMsBudget = "firstInteractiveMs_budget"
            case phases
        }
    }

    struct Phase: Decodable, Identifiable {
        let phase: Int
        let label: String
        let blocking: Bool
        let needsBlender: Bool
        var assets: [String] = []
        var unlocks: [String] = []
        var id: Int { phase }
    }

    struct PlaceholderScene: Decodable {
        let anchor: String
        let title: String
        var firstBeat: String = ""
        var placeholders: [Placeholder] = []
        var needsBlender: Bool = false
    }

    struct Placeholder: Decodable, Identifiable {
        let assetId: String
        var primitive: String = "box"
        var positionYup: [Double] = []
        let footprintM: RuntimeSceneContract.Footprint
        var label: String = ""
        var id: String { assetId }
        enum CodingKeys: String, CodingKey {
            case assetId, primitive, label
            case positionYup = "position_yup"
            case footprintM = "footprint_m"
        }
    }
}

// The delivery_url body: the modular assets the factory produced (or predicted).
// On device we don't load the GLBs (RealityKit can't); we use it for animation
// intent + per-asset status. Real models are a later USDZ-export follow-on.
struct DeliveryPackage: Decodable {
    let experienceId: String
    var assets: [Asset] = []
    var animations: [Animation] = []

    struct Asset: Decodable, Identifiable {
        let assetId: String
        var path: String = ""
        var status: String = "dry_run"
        var metadata: Meta?
        var id: String { assetId }
        struct Meta: Decodable {
            var semanticRole: String = ""
            var realWorldScale: [Double] = []
        }
    }

    struct Animation: Decodable, Identifiable {
        let animationId: String
        var targetAssets: [String] = []
        let type: String                   // looping | assemble | reciprocate | orbit | spin
        var path: String = ""
        var id: String { animationId }
    }
}

/// What GenerativeClient.generateRepresentation returns: the three decoded
/// artifacts a representation job produces, ready for the RepresentationRuntime.
struct RepresentationBundle {
    let contract: RuntimeSceneContract
    let progressive: ProgressiveBundle
    let delivery: DeliveryPackage
}
