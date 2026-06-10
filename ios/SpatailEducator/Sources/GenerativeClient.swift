import Foundation

// Client for the generative AR loop (see docs/generative_ar_contract.md):
//   POST /jobs        -> { id, status }
//   GET  /jobs/{id}   -> { status, stage, message, usdz_url?, metadata_url? }
//   GET  /artifacts/* -> USDZ bytes
//
// Reachable over Tailscale: the user sets the PC's tailnet base URL once; it's
// stored in UserDefaults. No LAN/IP assumptions baked in.

enum GenStatus: String, Decodable {
    case queued, running, done, error
}

struct CreateJobResponse: Decodable {
    let id: String
    let status: GenStatus
}

struct JobState: Decodable {
    let id: String
    let status: GenStatus
    let stage: String?
    let message: String?
    let usdz_url: String?
    let metadata_url: String?
    // queued jobs waiting for a Blender lane: how many are ahead (per-phone scheduler).
    var queuePosition: Int? = nil
    // experience jobs (mode == "experience"):
    var experience_url: String? = nil
    var usdz_base: String? = nil
    var stations: Int? = nil
    var title: String? = nil
    // representation jobs (mode == "representation"):
    var runtime_url: String? = nil
    var delivery_url: String? = nil
    var progressive_url: String? = nil
    var plan_url: String? = nil
}

/// A downloaded experience: the decoded spec + a local folder holding all station USDZs.
struct DownloadedExperience {
    let spec: ExperienceSpec
    let folder: URL          // station USDZs live here, named per spec hero.usdz
}

enum GenError: LocalizedError {
    case noServer
    case http(Int)
    case server(String)
    case timeout
    case badURL

    var errorDescription: String? {
        switch self {
        case .noServer: return "Set the PC server address in Settings first."
        case .http(let c): return "Server returned HTTP \(c)."
        case .server(let m): return m
        case .timeout: return "Generation timed out. Try again."
        case .badURL: return "The server address looks invalid."
        }
    }
}

@MainActor
final class GenerativeClient {
    // Persisted base URL, e.g. "http://mansourspc.tailnet-xxxx.ts.net:8787"
    static let baseURLKey = "spatail.gen.baseURL"

    static var baseURL: String {
        get { UserDefaults.standard.string(forKey: baseURLKey) ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: baseURLKey) }
    }

    // A stable id for THIS running app instance. Sent with every job so the PC
    // binds this phone to its own Blender session/lane — builds from different
    // phones run concurrently in their own headless Blender instead of queuing
    // behind each other on one shared instance. Fresh per launch ("per running app").
    static let sessionId: String = UUID().uuidString

    private func base() throws -> URL {
        let s = Self.baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { throw GenError.noServer }
        guard let u = URL(string: s) else { throw GenError.badURL }
        return u
    }

    // Resolve an artifact URL against the base the client actually reaches.
    //
    // The server may hand back an ABSOLUTE url built from its own internal host
    // (e.g. http://localhost:8787/... or http://127.0.0.1:8787/...). Over
    // Tailscale the phone can't reach the server's localhost, so we must NOT use
    // such URLs verbatim — that's "unable to reach server" right after a job
    // finishes. Since one server serves both the API and the artifacts, we keep
    // only the path (+query) and re-host it onto `base()` — the address that
    // polling already succeeded against.
    private func resolve(_ path: String) throws -> URL {
        let b = try base()
        // Extract just path (+query), whether `path` is absolute or relative.
        var pathOnly = path
        var query: String? = nil
        if let abs = URLComponents(string: path), abs.scheme != nil {
            pathOnly = abs.path
            query = abs.query
        } else if let qi = path.firstIndex(of: "?") {
            pathOnly = String(path[..<qi])
            query = String(path[path.index(after: qi)...])
        }
        guard var comps = URLComponents(url: b, resolvingAgainstBaseURL: false) else {
            throw GenError.badURL
        }
        comps.path = pathOnly.hasPrefix("/") ? pathOnly : "/" + pathOnly
        comps.query = query
        guard let out = comps.url else { throw GenError.badURL }
        return out
    }

    /// Submit a prompt, poll to completion, download the USDZ, return its local file URL.
    /// `onStage` reports human-readable progress for the UI.
    func generate(prompt: String,
                  onStage: @escaping (String) -> Void) async throws -> URL {
        let create = try await submit(prompt: prompt)
        onStage("queued…")

        let deadline = Date().addingTimeInterval(300) // 5 min hard cap
        var state = JobState(id: create.id, status: create.status,
                             stage: "queued", message: nil,
                             usdz_url: nil, metadata_url: nil)
        while true {
            if Date() > deadline { throw GenError.timeout }
            if state.status == .done { break }
            if state.status == .error { throw GenError.server(state.message ?? "Generation failed.") }
            try await Task.sleep(nanoseconds: 2_000_000_000) // poll every 2s
            state = try await poll(id: create.id)
            onStage(state.stage ?? state.status.rawValue)
        }
        guard let usdz = state.usdz_url else { throw GenError.server("Job done but no USDZ produced.") }
        onStage("downloading…")
        return try await download(path: usdz, id: create.id)
    }

    /// Submit a prompt as a full multi-station EXPERIENCE, poll to completion,
    /// download the spec + every station USDZ, and return them. Longer cap because
    /// the Director drives Blender once per station.
    func generateExperience(prompt: String,
                            onStage: @escaping (String) -> Void) async throws -> DownloadedExperience {
        let create = try await submit(prompt: prompt, mode: "experience")
        onStage("planning…")

        let deadline = Date().addingTimeInterval(600) // 10 min — multi-station build
        var state = try await poll(id: create.id)
        while state.status != .done {
            if Date() > deadline { throw GenError.timeout }
            if state.status == .error { throw GenError.server(state.message ?? "Generation failed.") }
            try await Task.sleep(nanoseconds: 2_500_000_000)
            state = try await poll(id: create.id)
            onStage(state.stage ?? state.status.rawValue)
        }
        guard let expURL = state.experience_url else {
            throw GenError.server("Job done but no experience produced.")
        }
        onStage("downloading experience…")

        // download + decode the spec
        let specURL = try resolve(expURL)
        let (specData, specResp) = try await URLSession.shared.data(from: specURL)
        try check(specResp)
        let spec = try ExperienceSpec.decode(specData)

        // a dedicated folder for this experience's USDZs
        let folder = FileManager.default.temporaryDirectory
            .appendingPathComponent("exp_\(create.id)", isDirectory: true)
        try? FileManager.default.removeItem(at: folder)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)

        // download each station hero USDZ into the folder under its spec name
        let base = state.usdz_base ?? "/artifacts/"
        for (i, station) in spec.orderedStations.enumerated() {
            onStage("downloading model \(i + 1)/\(spec.stations.count)…")
            let url = try resolve(base + station.hero.usdz)
            let (tmp, resp) = try await URLSession.shared.download(from: url)
            try check(resp)
            let dst = folder.appendingPathComponent(station.hero.usdz)
            try? FileManager.default.removeItem(at: dst)
            try FileManager.default.moveItem(at: tmp, to: dst)
        }
        return DownloadedExperience(spec: spec, folder: folder)
    }

    /// Submit a prompt as a REPRESENTATION job (the new brain), poll (short cap —
    /// the server runs dry-run, so it's seconds not minutes), then download the
    /// three artifacts concurrently and decode them into a RepresentationBundle.
    func generateRepresentation(prompt: String,
                                onStage: @escaping (String) -> Void) async throws -> RepresentationBundle {
        let create = try await submit(prompt: prompt, mode: "representation")
        return try await awaitBundle(create, onStage: onStage)
    }

    /// Educational Wrapper: a selected concept (+ its surrounding context) becomes the
    /// SAME runtime bundle, built by the representation pipeline server-side, so the
    /// existing RepresentationRuntime renders it. No new scene logic on the client.
    func generateEducational(selectedText: String, surroundingContext: String,
                             onStage: @escaping (String) -> Void) async throws -> RepresentationBundle {
        let create = try await submit(prompt: selectedText, mode: "educational",
                                      extra: ["selectedText": selectedText,
                                              "surroundingContext": surroundingContext])
        return try await awaitBundle(create, onStage: onStage)
    }

    /// Poll a runtime-shaped job (representation or educational) to completion and
    /// download its three artifacts concurrently into a RepresentationBundle.
    private func awaitBundle(_ create: CreateJobResponse,
                             onStage: @escaping (String) -> Void) async throws -> RepresentationBundle {
        onStage("planning…")
        let deadline = Date().addingTimeInterval(120)
        var state = try await poll(id: create.id)
        while state.status != .done {
            if Date() > deadline { throw GenError.timeout }
            if state.status == .error { throw GenError.server(state.message ?? "Generation failed.") }
            try await Task.sleep(nanoseconds: 1_000_000_000)
            state = try await poll(id: create.id)
            onStage(state.stage ?? state.status.rawValue)
        }
        guard let rURL = state.runtime_url, let pURL = state.progressive_url,
              let dURL = state.delivery_url else {
            throw GenError.server("Job done but no experience produced.")
        }
        onStage("downloading…")
        async let contract: RuntimeSceneContract = fetchJSON(rURL)
        async let progressive: ProgressiveBundle = fetchJSON(pURL)
        async let delivery: DeliveryPackage = fetchJSON(dURL)
        return RepresentationBundle(contract: try await contract,
                                    progressive: try await progressive,
                                    delivery: try await delivery)
    }

    private func fetchJSON<T: Decodable>(_ path: String) async throws -> T {
        let url = try resolve(path)
        let (data, resp) = try await URLSession.shared.data(from: url)
        try check(resp)
        return try JSONDecoder().decode(T.self, from: data)
    }

    // MARK: - modular experience — synchronous POST /modular
    /// Pasted text -> the modular experience contract. `tracked` selects the
    /// object-TRACKED stream: the server answers with anchoring.mode == "object"
    /// (+ the .referenceobject to download) and the design system tailors the
    /// experience to overlay the real object instead of placing it in space.
    func generateModular(text: String, tracked: Bool = false,
                         trackedObjectId: String = "",
                         referenceObjectUrl: String = "") async throws -> ModularExperience {
        try await postModular(trackedPayload(["kind": "text", "text": text, "use_llm": true],
                                             tracked, trackedObjectId, referenceObjectUrl))
    }

    /// Phone photo (JPEG bytes) + the user's question -> Gemini mechanism brief ->
    /// the same modular experience (with a generationJobId for the Blender build).
    func generateModular(imageJPEG: Data, note: String = "", question: String = "",
                         tracked: Bool = false, trackedObjectId: String = "",
                         referenceObjectUrl: String = "") async throws -> ModularExperience {
        try await postModular(trackedPayload(
            ["kind": "image", "image": imageJPEG.base64EncodedString(),
             "mime": "image/jpeg", "note": note, "question": question, "use_llm": true],
            tracked, trackedObjectId, referenceObjectUrl))
    }

    private func trackedPayload(_ base: [String: Any], _ tracked: Bool,
                                _ objectId: String, _ refUrl: String) -> [String: Any] {
        guard tracked else { return base }
        var p = base
        p["tracked"] = true
        p["trackedObjectId"] = objectId
        p["referenceObjectUrl"] = refUrl
        return p
    }

    // MARK: - trackables (the reference-object library for the TRACKED stream)
    /// One Mac-trained .referenceobject the server delivers post-compile.
    struct Trackable: Decodable, Identifiable {
        let id: String
        var name = ""
        var url = ""                 // server-relative: /trackables/<file>
        var tracking = "detection"   // "detection" (stationary) | "tracking" (handheld)
        var subjects: [String] = []
        enum K: String, CodingKey { case id, name, url, tracking, subjects }
        init(from d: Decoder) throws {
            let c = try d.container(keyedBy: K.self)
            id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
            name = (try? c.decode(String.self, forKey: .name)) ?? ""
            url = (try? c.decode(String.self, forKey: .url)) ?? ""
            tracking = (try? c.decode(String.self, forKey: .tracking)) ?? "detection"
            subjects = (try? c.decode([String].self, forKey: .subjects)) ?? []
        }
    }
    private struct TrackablesIndex: Decodable { var trackables: [Trackable] = [] }

    /// GET /trackables — which real-world objects this server can track right now.
    func fetchTrackables() async throws -> [Trackable] {
        let idx: TrackablesIndex = try await fetchJSON("/trackables")
        return idx.trackables
    }

    /// Poll a queued Blender generation job (mode:object) to completion, then download
    /// its USDZ to a local file. Long cap — Blender authoring runs minutes, not seconds.
    func awaitGeneratedModel(jobId: String,
                             onStage: @escaping (String) -> Void) async throws -> URL {
        let deadline = Date().addingTimeInterval(600)        // 10 min
        var state = try await poll(id: jobId)
        while state.status != .done {
            if Date() > deadline { throw GenError.timeout }
            if state.status == .error { throw GenError.server(state.message ?? "Generation failed.") }
            try await Task.sleep(nanoseconds: 2_500_000_000)
            state = try await poll(id: jobId)
            onStage(state.stage ?? state.status.rawValue)
        }
        guard let usdz = state.usdz_url else { throw GenError.server("Job done but no model produced.") }
        return try await download(path: usdz, id: jobId)
    }

    private func postModular(_ payload: [String: Any]) async throws -> ModularExperience {
        var req = URLRequest(url: try base().appendingPathComponent("modular"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 120          // synchronous server-side build (engine + agent)
        var payload = payload
        payload["session"] = Self.sessionId   // bind this phone to its own Blender lane
        req.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try check(resp)
        return try JSONDecoder().decode(ModularExperience.self, from: data)
    }

    // MARK: - project (iterative) editing
    /// Decoded experience + the RAW server JSON (persisted verbatim into the project
    /// bundle, so we don't need the whole contract to be Encodable).
    struct ModularResult { let experience: ModularExperience; let raw: Data }

    /// First prompt OR an edit. `prior` carries the current scene's subject+summary
    /// so the server EVOLVES the existing experience instead of starting from zero.
    /// `tracked` selects the object-TRACKED stream (overlay the real object).
    func generateProject(text: String,
                         prior: (subject: String, summary: String)? = nil,
                         tracked: Bool = false, trackedObjectId: String = "",
                         referenceObjectUrl: String = "") async throws -> ModularResult {
        var payload: [String: Any] = ["kind": "text", "text": text, "use_llm": true]
        if let p = prior { payload["prior"] = ["subject": p.subject, "summary": p.summary] }
        payload = trackedPayload(payload, tracked, trackedObjectId, referenceObjectUrl)
        return try await postModularRaw(payload)
    }

    private func postModularRaw(_ payload: [String: Any]) async throws -> ModularResult {
        var req = URLRequest(url: try base().appendingPathComponent("modular"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 120
        var payload = payload
        payload["session"] = Self.sessionId
        req.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try check(resp)
        let exp = try JSONDecoder().decode(ModularExperience.self, from: data)
        return ModularResult(experience: exp, raw: data)
    }

    private func submit(prompt: String, mode: String? = nil,
                        extra: [String: Any] = [:]) async throws -> CreateJobResponse {
        var req = URLRequest(url: try base().appendingPathComponent("jobs"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var payload: [String: Any] = ["prompt": prompt, "client": "ios",
                                      "session": Self.sessionId]
        if let mode { payload["mode"] = mode }
        for (k, v) in extra { payload[k] = v }
        req.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try check(resp)
        return try JSONDecoder().decode(CreateJobResponse.self, from: data)
    }

    private func poll(id: String) async throws -> JobState {
        let url = try base().appendingPathComponent("jobs").appendingPathComponent(id)
        let (data, resp) = try await URLSession.shared.data(from: url)
        try check(resp)
        return try JSONDecoder().decode(JobState.self, from: data)
    }

    private func download(path: String, id: String) async throws -> URL {
        let url = try resolve(path)
        let (tmp, resp) = try await URLSession.shared.download(from: url)
        try check(resp)
        // Move to a stable, RealityKit-loadable .usdz path in caches.
        let dst = FileManager.default.temporaryDirectory
            .appendingPathComponent("gen_\(id).usdz")
        try? FileManager.default.removeItem(at: dst)
        try FileManager.default.moveItem(at: tmp, to: dst)
        return dst
    }

    private func check(_ resp: URLResponse) throws {
        guard let http = resp as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            throw GenError.http(http.statusCode)
        }
    }
}
