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
    /// Set by the server when a live-Blender build was queued for the primary
    /// object; the client polls this job and streams the real model in over the box.
    var generationJobId: String? = nil
    /// v0.6 Scene Contract (content/placement/brand/logic). Optional + additive: the
    /// runtime uses logic.triggers for the game-manager layer when present; absent on
    /// older server builds, in which case the runtime falls back to beats + taps.
    var sceneContract: SceneContract? = nil

    // Tolerant decoders: every field falls back to a default when the key is
    // missing/null/mistyped, so a partial trigger graph (e.g. a `when` with no
    // `params`) can NEVER fail the whole experience decode. Swift's *synthesized*
    // Decodable does NOT use property defaults for missing keys — these custom
    // inits do.
    struct SceneContract: Decodable {
        var placement = ScenePlacement()
        var logic = Logic()
        init() {}
        init(from d: Decoder) throws {
            let c = try d.container(keyedBy: K.self)
            placement = (try? c.decode(ScenePlacement.self, forKey: .placement)) ?? ScenePlacement()
            logic = (try? c.decode(Logic.self, forKey: .logic)) ?? Logic()
        }
        enum K: String, CodingKey { case placement, logic }

        struct ScenePlacement: Decodable {
            var anchorPreference = "table"
            var layout = "arc"
            var scaleMode = "dynamic"
            var primary: String? = nil
            init() {}
            init(from d: Decoder) throws {
                let c = try d.container(keyedBy: K.self)
                anchorPreference = (try? c.decode(String.self, forKey: .anchorPreference)) ?? "table"
                layout = (try? c.decode(String.self, forKey: .layout)) ?? "arc"
                scaleMode = (try? c.decode(String.self, forKey: .scaleMode)) ?? "dynamic"
                primary = try? c.decode(String.self, forKey: .primary)
            }
            enum K: String, CodingKey { case anchorPreference, layout, scaleMode, primary }
        }
        struct Logic: Decodable {
            var triggers: [Trigger] = []
            var objectives: [Objective] = []
            init() {}
            init(from d: Decoder) throws {
                let c = try d.container(keyedBy: K.self)
                triggers = (try? c.decode([Trigger].self, forKey: .triggers)) ?? []
                objectives = (try? c.decode([Objective].self, forKey: .objectives)) ?? []
            }
            enum K: String, CodingKey { case triggers, objectives }
        }
        struct Trigger: Decodable {
            var when = When()
            var doActions: [Action] = []
            init() {}
            init(from d: Decoder) throws {
                let c = try d.container(keyedBy: K.self)
                when = (try? c.decode(When.self, forKey: .when)) ?? When()
                doActions = (try? c.decode([Action].self, forKey: .doActions)) ?? []
            }
            enum K: String, CodingKey { case when; case doActions = "do" }
        }
        struct When: Decodable {
            var event = ""                 // onTap | onApproach | onGaze | onGrab | onQuizCorrect
            var target = "scene"
            var params: [String: AnyParam] = [:]
            init() {}
            init(from d: Decoder) throws {
                let c = try d.container(keyedBy: K.self)
                event = (try? c.decode(String.self, forKey: .event)) ?? ""
                target = (try? c.decode(String.self, forKey: .target)) ?? "scene"
                params = (try? c.decode([String: AnyParam].self, forKey: .params)) ?? [:]
            }
            enum K: String, CodingKey { case event, target, params }
        }
        struct Action: Decodable {
            var action = ""                // advanceTrack | mechanic | playClipIfAny | playSound | ...
            var mechanic: String? = nil
            var target: String? = nil
            var params: [String: AnyParam] = [:]
            init() {}
            init(from d: Decoder) throws {
                let c = try d.container(keyedBy: K.self)
                action = (try? c.decode(String.self, forKey: .action)) ?? ""
                mechanic = try? c.decode(String.self, forKey: .mechanic)
                target = try? c.decode(String.self, forKey: .target)
                params = (try? c.decode([String: AnyParam].self, forKey: .params)) ?? [:]
            }
            enum K: String, CodingKey { case action, mechanic, target, params }
        }
        struct Objective: Decodable {
            var id = "", goal = "", summary = ""
            init() {}
            init(from d: Decoder) throws {
                let c = try d.container(keyedBy: K.self)
                id = (try? c.decode(String.self, forKey: .id)) ?? ""
                goal = (try? c.decode(String.self, forKey: .goal)) ?? ""
                summary = (try? c.decode(String.self, forKey: .summary)) ?? ""
            }
            enum K: String, CodingKey { case id, goal, summary }
        }
    }

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
