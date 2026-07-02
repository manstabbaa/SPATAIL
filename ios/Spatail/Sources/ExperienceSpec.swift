import Foundation

// Swift mirror of the SPATAIL Experience Spec (docs/experience_spec_contract.md, v0.1).
// The post-compile XR payload: the app reads this live to assemble an interactive
// education experience — no recompile. Decoded from experience.json the Director
// produces. Shared across iOS + visionOS (platform-neutral data).

struct ExperienceSpec: Decodable {
    let specVersion: String
    let id: String
    let title: String
    var subject: String = "general"
    var prompt: String = ""
    var summary: String = ""
    var narrationTone: String = "warm, curious, classroom"
    var placement = Placement()
    let stations: [Station]

    enum CodingKeys: String, CodingKey {
        case specVersion = "spec_version"
        case id, title, subject, prompt, summary
        case narrationTone = "narration_tone"
        case placement, stations
    }

    struct Placement: Decodable {
        var anchor: String = "table"          // table | floor | free
        var layout: String = "arc"            // arc | line | cluster
        var comfortRadiusM: Double = 1.4      // <= 1.5 (Apple HIG comfort bubble)
        var facing: String = "user"
        var spacingMinM: Double = 0.18
        var guided: Bool = true

        enum CodingKeys: String, CodingKey {
            case anchor, layout, facing
            case comfortRadiusM = "comfort_radius_m"
            case spacingMinM = "spacing_min_m"
            case guided
        }
    }

    struct Station: Decodable, Identifiable {
        let id: String
        let order: Int
        let title: String
        var subtitle: String = ""
        var narration: String = ""
        let hero: Hero
        var panels: [Panel] = []
        var mechanics: [Mechanic] = []
    }

    struct Hero: Decodable {
        let usdz: String
        var animation: String = "baked"       // baked | none
        let footprintM: Footprint
        var scaleMode: String = "dynamic"     // dynamic | fixed

        enum CodingKeys: String, CodingKey {
            case usdz, animation
            case footprintM = "footprint_m"
            case scaleMode = "scale_mode"
        }
    }

    struct Footprint: Decodable { let w: Double; let d: Double; let h: Double }

    struct Panel: Decodable, Identifiable {
        let id: String
        let kind: String                      // title | fact | data | caption | quiz
        var title: String = ""
        var body: String = ""
        var anchor: String = "above_hero"     // above_hero | beside_left | beside_right | below_hero
        var billboard: Bool = true
        var reveal: String = "on_focus"       // on_focus | on_tap | always
        // quiz-only
        var question: String = ""
        var options: [String] = []
        var answerIndex: Int? = nil

        enum CodingKeys: String, CodingKey {
            case id, kind, title, body, anchor, billboard, reveal
            case question, options
            case answerIndex = "answer_index"
        }
    }

    struct Mechanic: Decodable {
        let type: String                      // play_baked | tap_reveal | grab_physics | quiz_panel
        var params: [String: JSONValue] = [:]
    }
}

// Minimal JSON value so Mechanic.params can carry mixed types without a fixed schema.
enum JSONValue: Decodable {
    case string(String), number(Double), bool(Bool), null
    case array([JSONValue]), object([String: JSONValue])

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null; return }
        if let b = try? c.decode(Bool.self) { self = .bool(b); return }
        if let n = try? c.decode(Double.self) { self = .number(n); return }
        if let s = try? c.decode(String.self) { self = .string(s); return }
        if let a = try? c.decode([JSONValue].self) { self = .array(a); return }
        if let o = try? c.decode([String: JSONValue].self) { self = .object(o); return }
        throw DecodingError.dataCorruptedError(in: c, debugDescription: "unsupported JSON value")
    }

    var stringValue: String? { if case .string(let s) = self { return s }; return nil }
    var doubleValue: Double? { if case .number(let n) = self { return n }; return nil }
    var boolValue: Bool? { if case .bool(let b) = self { return b }; return nil }
    var intValue: Int? { if case .number(let n) = self { return Int(n) }; return nil }
    var arrayValue: [JSONValue]? { if case .array(let a) = self { return a }; return nil }
}

extension ExperienceSpec {
    /// Decode from downloaded experience.json bytes.
    static func decode(_ data: Data) throws -> ExperienceSpec {
        try JSONDecoder().decode(ExperienceSpec.self, from: data)
    }
    /// Major-version compatibility check against the runtime.
    var isCompatible: Bool {
        specVersion.split(separator: ".").first.map(String.init) == "0"
    }
    var orderedStations: [Station] { stations.sorted { $0.order < $1.order } }
}
