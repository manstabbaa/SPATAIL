import Foundation

// Swift mirror of the SPATAIL v0.6 modular contract — what POST /modular returns
// (studio/director/experience.py). Motion is BAKED into each asset as named CLIPS;
// the experience is a SEQUENCE of steps that play those clips, plus interaction
// TRIGGERS. SPATAIL (ModularRuntime) is the game-manager that plays & sequences.
// (The old procedural "mechanics catalog" is gone.)
//
// Every nested type decodes tolerantly (missing/null/mistyped key -> default), so a
// partial payload can never fail the whole decode.

struct ModularExperience: Decodable {
    let schemaVersion: String
    let experienceId: String
    let title: String
    var understanding = Understanding()
    var stage = Stage()
    var assets: [Asset] = []
    var composer = "deterministic"
    var sequence: [Step] = []
    var triggers: [Trigger] = []
    var clips: [Clip] = []
    var generationJobId: String? = nil

    enum K: String, CodingKey {
        case schemaVersion, experienceId, title, understanding, stage, assets,
             composer, sequence, triggers, clips, generationJobId
    }
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        schemaVersion = (try? c.decode(String.self, forKey: .schemaVersion)) ?? "0.6"
        experienceId = (try? c.decode(String.self, forKey: .experienceId)) ?? UUID().uuidString
        title = (try? c.decode(String.self, forKey: .title)) ?? "Experience"
        understanding = (try? c.decode(Understanding.self, forKey: .understanding)) ?? Understanding()
        stage = (try? c.decode(Stage.self, forKey: .stage)) ?? Stage()
        assets = (try? c.decode([Asset].self, forKey: .assets)) ?? []
        composer = (try? c.decode(String.self, forKey: .composer)) ?? "deterministic"
        sequence = (try? c.decode([Step].self, forKey: .sequence)) ?? []
        triggers = (try? c.decode([Trigger].self, forKey: .triggers)) ?? []
        clips = (try? c.decode([Clip].self, forKey: .clips)) ?? []
        generationJobId = try? c.decode(String.self, forKey: .generationJobId)
    }

    struct Understanding: Decodable {
        var domain = "", intent = "", subject = "", summary = "", reasoning = ""
        init() {}
        init(from d: Decoder) throws {
            let c = try d.container(keyedBy: K.self)
            domain = (try? c.decode(String.self, forKey: .domain)) ?? ""
            intent = (try? c.decode(String.self, forKey: .intent)) ?? ""
            subject = (try? c.decode(String.self, forKey: .subject)) ?? ""
            summary = (try? c.decode(String.self, forKey: .summary)) ?? ""
            reasoning = (try? c.decode(String.self, forKey: .reasoning)) ?? ""
        }
        enum K: String, CodingKey { case domain, intent, subject, summary, reasoning }
    }

    struct Stage: Decodable {
        var anchor = "table", layout = "arc", facing = "user", scaleMode = "dynamic"
        init() {}
        init(from d: Decoder) throws {
            let c = try d.container(keyedBy: K.self)
            anchor = (try? c.decode(String.self, forKey: .anchor)) ?? "table"
            layout = (try? c.decode(String.self, forKey: .layout)) ?? "arc"
            facing = (try? c.decode(String.self, forKey: .facing)) ?? "user"
            scaleMode = (try? c.decode(String.self, forKey: .scaleMode)) ?? "dynamic"
        }
        enum K: String, CodingKey { case anchor, layout, facing, scaleMode }
    }

    struct Asset: Decodable, Identifiable {
        let id: String
        var name = "", role = "part", description = ""
        var glbUrl = "", usdzUrl = "", fallbackPrimitive = "cube"
        var scaleMeters: [Double] = [0.2, 0.2, 0.2]
        var supportsAnimation = false, supportsHighlight = false, supportsTransparency = false
        var status = "placeholder"
        var clips: [Clip] = []
        var parts: [Part] = []
        init(from d: Decoder) throws {
            let c = try d.container(keyedBy: K.self)
            id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
            name = (try? c.decode(String.self, forKey: .name)) ?? ""
            role = (try? c.decode(String.self, forKey: .role)) ?? "part"
            description = (try? c.decode(String.self, forKey: .description)) ?? ""
            glbUrl = (try? c.decode(String.self, forKey: .glbUrl)) ?? ""
            usdzUrl = (try? c.decode(String.self, forKey: .usdzUrl)) ?? ""
            fallbackPrimitive = (try? c.decode(String.self, forKey: .fallbackPrimitive)) ?? "cube"
            scaleMeters = (try? c.decode([Double].self, forKey: .scaleMeters)) ?? [0.2, 0.2, 0.2]
            supportsAnimation = (try? c.decode(Bool.self, forKey: .supportsAnimation)) ?? false
            supportsHighlight = (try? c.decode(Bool.self, forKey: .supportsHighlight)) ?? false
            supportsTransparency = (try? c.decode(Bool.self, forKey: .supportsTransparency)) ?? false
            status = (try? c.decode(String.self, forKey: .status)) ?? "placeholder"
            clips = (try? c.decode([Clip].self, forKey: .clips)) ?? []
            parts = (try? c.decode([Part].self, forKey: .parts)) ?? []
        }
        enum K: String, CodingKey {
            case id, name, role, description, glbUrl, usdzUrl, fallbackPrimitive,
                 scaleMeters, supportsAnimation, supportsHighlight, supportsTransparency,
                 status, clips, parts
        }
    }

    struct Part: Decodable {
        var name = "", role = "part"
        init(from d: Decoder) throws {
            let c = try d.container(keyedBy: K.self)
            name = (try? c.decode(String.self, forKey: .name)) ?? ""
            role = (try? c.decode(String.self, forKey: .role)) ?? "part"
        }
        enum K: String, CodingKey { case name, role }
    }

    struct Clip: Decodable {
        var name = "demo", role = "demo", start = 1, end = 120, fps = 30, loop = true
        init() {}
        init(from d: Decoder) throws {
            let c = try d.container(keyedBy: K.self)
            name = (try? c.decode(String.self, forKey: .name)) ?? "demo"
            role = (try? c.decode(String.self, forKey: .role)) ?? "demo"
            start = (try? c.decode(Int.self, forKey: .start)) ?? 1
            end = (try? c.decode(Int.self, forKey: .end)) ?? 120
            fps = (try? c.decode(Int.self, forKey: .fps)) ?? 30
            loop = (try? c.decode(Bool.self, forKey: .loop)) ?? true
        }
        enum K: String, CodingKey { case name, role, start, end, fps, loop }
    }

    struct Step: Decodable, Identifiable {
        var id = ""
        var title = "", narration = "", focus = "scene", clip = "demo", advance = "tap"
        var loop = true
        var panels: [Panel] = []
        init(from d: Decoder) throws {
            let c = try d.container(keyedBy: K.self)
            id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
            title = (try? c.decode(String.self, forKey: .title)) ?? ""
            narration = (try? c.decode(String.self, forKey: .narration)) ?? ""
            focus = (try? c.decode(String.self, forKey: .focus)) ?? "scene"
            clip = (try? c.decode(String.self, forKey: .clip)) ?? "demo"
            advance = (try? c.decode(String.self, forKey: .advance)) ?? "tap"
            loop = (try? c.decode(Bool.self, forKey: .loop)) ?? true
            panels = (try? c.decode([Panel].self, forKey: .panels)) ?? []
        }
        enum K: String, CodingKey { case id, title, narration, focus, clip, advance, loop, panels }
    }

    struct Panel: Decodable {
        var kind = "fact", title = "", body = "", question = ""
        var options: [String] = []
        var answer = 0
        init(from d: Decoder) throws {
            let c = try d.container(keyedBy: K.self)
            kind = (try? c.decode(String.self, forKey: .kind)) ?? "fact"
            title = (try? c.decode(String.self, forKey: .title)) ?? ""
            body = (try? c.decode(String.self, forKey: .body)) ?? ""
            question = (try? c.decode(String.self, forKey: .question)) ?? ""
            options = (try? c.decode([String].self, forKey: .options)) ?? []
            answer = (try? c.decode(Int.self, forKey: .answer)) ?? 0
        }
        enum K: String, CodingKey { case kind, title, body, question, options, answer }
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
        var event = "", target = "scene"
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
        var action = ""
        var clip: String? = nil
        var target: String? = nil
        var params: [String: AnyParam] = [:]
        init(from d: Decoder) throws {
            let c = try d.container(keyedBy: K.self)
            action = (try? c.decode(String.self, forKey: .action)) ?? ""
            clip = try? c.decode(String.self, forKey: .clip)
            target = try? c.decode(String.self, forKey: .target)
            params = (try? c.decode([String: AnyParam].self, forKey: .params)) ?? [:]
        }
        enum K: String, CodingKey { case action, clip, target, params }
    }
}

/// A tolerant scalar/array param value (trigger params are open-ended JSON).
struct AnyParam: Decodable {
    let double: Double?
    let string: String?
    let bool: Bool?
    let strings: [String]?

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let v = try? c.decode(Bool.self)     { bool = v; double = nil; string = nil; strings = nil; return }
        if let v = try? c.decode(Double.self)   { double = v; bool = nil; string = nil; strings = nil; return }
        if let v = try? c.decode(String.self)   { string = v; double = nil; bool = nil; strings = nil; return }
        if let v = try? c.decode([String].self) { strings = v; double = nil; bool = nil; string = nil; return }
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
