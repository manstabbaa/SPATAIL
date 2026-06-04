import Foundation

// Swift mirror of the SPATAIL v0.5 MODULAR experience contract — what POST /modular
// returns (studio/director/experience.py). Platform-neutral (iOS / visionOS / web).
// The agent composed `beats`, each applying `mechanics` (from public/mechanics/
// mechanics.json) to a target asset or the scene. ModularRuntime executes them
// generically by switching on the mechanic's runtime key.

struct ModularExperience: Decodable {
    let schemaVersion: String
    let experienceId: String
    let title: String
    var understanding = Understanding()
    var stage = Stage()
    var assets: [Asset] = []
    var composer: String = "deterministic"
    var mechanicsUsed: [String] = []
    var capabilities: [String] = []
    var beats: [Beat] = []
    var progressive = Progressive()

    struct Understanding: Decodable {
        var domain = "", intent = "", subject = "", summary = "", reasoning = ""
    }
    struct Stage: Decodable {
        var anchor = "table", layout = "arc", facing = "user", scaleMode = "dynamic"
    }
    struct Asset: Decodable, Identifiable {
        let id: String
        var name = ""
        var role = "part"                 // primary_object | comparison_object | part | label
        var description = ""
        var glbUrl = ""
        var usdzUrl = ""
        var fallbackPrimitive = "cube"
        var scaleMeters: [Double] = [0.2, 0.2, 0.2]
        var supportsAnimation = false
        var supportsHighlight = false
        var supportsTransparency = false
        var status = "placeholder"        // processed | placeholder
    }
    struct Beat: Decodable, Identifiable {
        let id: String
        var title = ""
        var narration = ""
        var focus = ""
        var mechanics: [MechanicUse] = []
    }
    struct MechanicUse: Decodable {
        let mechanic: String              // catalog id, e.g. "oscillate", "explode", "label"
        var target = "scene"              // assetId or "scene"
        var params: [String: AnyParam] = [:]
        var trigger = "auto"              // auto | on_tap | on_focus | on_step | on_grab | on_slider
    }
    struct Progressive: Decodable {
        var firstInteractiveMsBudget = 800
        var phase0Assets: [String] = []
        var needsGeneration: [String] = []
    }
}

/// A tolerant scalar/array param value (mechanics params are open-ended JSON).
/// Reads bool/number/string/string-array; everything else is nil-accessible.
struct AnyParam: Decodable {
    let double: Double?
    let string: String?
    let bool: Bool?
    let strings: [String]?

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let v = try? c.decode(Bool.self)       { bool = v; double = nil; string = nil; strings = nil; return }
        if let v = try? c.decode(Double.self)     { double = v; bool = nil; string = nil; strings = nil; return }
        if let v = try? c.decode(String.self)     { string = v; double = nil; bool = nil; strings = nil; return }
        if let v = try? c.decode([String].self)   { strings = v; double = nil; bool = nil; string = nil; return }
        double = nil; string = nil; bool = nil; strings = nil
    }
}

extension Dictionary where Key == String, Value == AnyParam {
    func d(_ k: String, _ dflt: Double) -> Double { self[k]?.double ?? dflt }
    func f(_ k: String, _ dflt: Float) -> Float { Float(self[k]?.double ?? Double(dflt)) }
    func s(_ k: String, _ dflt: String) -> String { self[k]?.string ?? dflt }
    func b(_ k: String, _ dflt: Bool) -> Bool { self[k]?.bool ?? dflt }
    func arr(_ k: String) -> [String] { self[k]?.strings ?? [] }
}
