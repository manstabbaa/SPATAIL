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
    var anchoring = Anchoring()            // the stream split: object (tracked) | world
    var placement = PlacementPolicy()      // Placement Design System §12 contract

    enum K: String, CodingKey {
        case schemaVersion, experienceId, title, understanding, stage, assets,
             composer, sequence, triggers, clips, generationJobId, anchoring, placement
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
        anchoring = (try? c.decode(Anchoring.self, forKey: .anchoring)) ?? Anchoring()
        placement = (try? c.decode(PlacementPolicy.self, forKey: .placement)) ?? PlacementPolicy()
    }

    /// The TRACKED/PLACED stream split (server: design_system.decide_anchoring).
    /// mode "object": mount the experience on an ARObjectAnchor resolved from a
    /// .referenceobject (iOS 27 object tracking); degrade to "world" per fallback.
    struct Anchoring: Decodable {
        var mode = "world"                  // "object" | "world"
        var objectId = ""
        var referenceObjectUrl = ""         // server-relative; downloaded at runtime
        var tracking = "detection"          // "detection" (stationary) | "tracking" (handheld)
        var pauseOnTrackingLoss = true
        var fallback = "world"
        init() {}
        init(from d: Decoder) throws {
            let c = try d.container(keyedBy: K.self)
            mode = (try? c.decode(String.self, forKey: .mode)) ?? "world"
            objectId = (try? c.decode(String.self, forKey: .objectId)) ?? ""
            referenceObjectUrl = (try? c.decode(String.self, forKey: .referenceObjectUrl)) ?? ""
            tracking = (try? c.decode(String.self, forKey: .tracking)) ?? "detection"
            pauseOnTrackingLoss = (try? c.decode(Bool.self, forKey: .pauseOnTrackingLoss)) ?? true
            fallback = (try? c.decode(String.self, forKey: .fallback)) ?? "world"
        }
        enum K: String, CodingKey {
            case mode, objectId, referenceObjectUrl, tracking, pauseOnTrackingLoss, fallback
        }
    }

    /// Placement Design System policy (docs/spatail-placement-design-system.md §12).
    /// POLICY only — anchor preference + fallback, scale mode + user-facing scale
    /// note, comfort, UI rules. The runtime/solver resolves geometry against the room.
    struct PlacementPolicy: Decodable {
        var preset = ""                     // e.g. "mechanical_exploded_view"
        var mode = ""                       // real_scale|miniature|exploded|object_overlay|wall_board|room_simulation
        var anchorType = "table"
        var anchorFallback = "floor"
        var scaleMode = "fit_to_surface"    // true_scale|fit_to_surface|miniature|enlarged_detail|adaptive
        var maxSurfaceCoverage = 0.65
        var showScaleReference = false
        var scaleNote = ""                  // §5: ALWAYS communicate changed scale
        var faceUser = true
        var preferredDistance = ""
        var labelBehavior = "on_select"
        var panelPosition = "beside_object"
        var semanticActions: [String] = []
        init() {}
        init(from d: Decoder) throws {
            let c = try d.container(keyedBy: K.self)
            preset = (try? c.decode(String.self, forKey: .preset)) ?? ""
            mode = (try? c.decode(String.self, forKey: .mode)) ?? ""
            if let a = try? c.decode([String: String].self, forKey: .anchor) {
                anchorType = a["type"] ?? "table"
                anchorFallback = a["fallback"] ?? "floor"
            }
            if let s = try? c.nestedContainer(keyedBy: ScaleK.self, forKey: .scale) {
                scaleMode = (try? s.decode(String.self, forKey: .mode)) ?? "fit_to_surface"
                maxSurfaceCoverage = (try? s.decode(Double.self, forKey: .maxSurfaceCoverage)) ?? 0.65
                showScaleReference = (try? s.decode(Bool.self, forKey: .showScaleReference)) ?? false
                scaleNote = (try? s.decode(String.self, forKey: .scaleNote)) ?? ""
            }
            if let p = try? c.nestedContainer(keyedBy: PosK.self, forKey: .position) {
                faceUser = (try? p.decode(Bool.self, forKey: .faceUser)) ?? true
            }
            if let u = try? c.nestedContainer(keyedBy: UiK.self, forKey: .ui) {
                labelBehavior = (try? u.decode(String.self, forKey: .labelBehavior)) ?? "on_select"
                panelPosition = (try? u.decode(String.self, forKey: .panelPosition)) ?? "beside_object"
            }
            if let m = try? c.nestedContainer(keyedBy: ComfK.self, forKey: .comfort) {
                preferredDistance = (try? m.decode(String.self, forKey: .preferredDistance)) ?? ""
            }
            if let i = try? c.nestedContainer(keyedBy: InterK.self, forKey: .interaction) {
                semanticActions = (try? i.decode([String].self, forKey: .semanticActions)) ?? []
            }
        }
        enum K: String, CodingKey { case preset, mode, anchor, scale, position, ui, comfort, interaction }
        enum ScaleK: String, CodingKey { case mode, maxSurfaceCoverage, showScaleReference, scaleNote }
        enum PosK: String, CodingKey { case faceUser }
        enum UiK: String, CodingKey { case labelBehavior, panelPosition }
        enum ComfK: String, CodingKey { case preferredDistance }
        enum InterK: String, CodingKey { case semanticActions }
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
        /// Spatial-UI anchoring (immersive callouts): `target` names a part of the
        /// focus model to highlight ("eye"); `anchorOffset` is a normalized [0..1]^3
        /// position inside the model's bounding box (the director can emit it from
        /// Gemini's multi-view understanding without any mesh splitting). Both
        /// optional — the runtime falls back to name-matching, then bbox heuristics.
        var target = ""
        var anchorOffset: [Double] = []
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
            target = (try? c.decode(String.self, forKey: .target))
                  ?? (try? c.decode(String.self, forKey: .highlight)) ?? ""
            anchorOffset = (try? c.decode([Double].self, forKey: .anchorOffset)) ?? []
        }
        enum K: String, CodingKey {
            case id, title, narration, focus, clip, advance, loop, panels,
                 target, highlight, anchorOffset
        }
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
