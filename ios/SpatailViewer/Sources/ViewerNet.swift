import Foundation

// Networking for the SPATAIL iOS Viewer — the on-device twin of webxr/viewer.js.
// Talks to the SAME PC brain (studio/server/job_server.py) and the SAME Gemini
// perception server (webxr/live/detector_server.py) the web viewer uses.
//
//   POST {brain}/modular     -> ModularExperience (+ generationJobId for Meshy builds)
//   GET  {brain}/jobs/{id}   -> JobState (9-stage Meshy progress; usdz_url/glb_url when done)
//   GET  {brain}/artifacts/* -> USDZ bytes (iOS renders USDZ natively in RealityKit)
//   POST {detector}/detect   -> [Detection] (live camera -> Gemini open-vocab boxes)

// MARK: - server config (persisted)

enum ViewerConfig {
    private static let brainKey = "spatail.viewer.brainURL"
    private static let detectorKey = "spatail.viewer.detectorURL"
    // Set these to your PC's Tailscale/LAN address in Settings on the device.
    static var brainURL: String {
        get { UserDefaults.standard.string(forKey: brainKey) ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: brainKey) }
    }
    static var detectorURL: String {
        get { UserDefaults.standard.string(forKey: detectorKey) ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: detectorKey) }
    }
}

// MARK: - decoded job state (mirrors job_server GET /jobs/{id})

struct JobState: Decodable {
    let id: String
    var status: String = "queued"
    var stage: String? = nil
    var message: String? = nil
    var usdz_url: String? = nil
    var glb_url: String? = nil
    var queuePosition: Int? = nil
    var stageIndex: Int? = nil
    var stageTotal: Int? = nil
    var percent: Int? = nil
    enum K: String, CodingKey { case id, status, stage, message, usdz_url, glb_url, queuePosition, stageIndex, stageTotal, percent }
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? ""
        status = (try? c.decode(String.self, forKey: .status)) ?? "queued"
        stage = try? c.decode(String.self, forKey: .stage)
        message = try? c.decode(String.self, forKey: .message)
        usdz_url = try? c.decode(String.self, forKey: .usdz_url)
        glb_url = try? c.decode(String.self, forKey: .glb_url)
        queuePosition = try? c.decode(Int.self, forKey: .queuePosition)
        stageIndex = try? c.decode(Int.self, forKey: .stageIndex)
        stageTotal = try? c.decode(Int.self, forKey: .stageTotal)
        percent = try? c.decode(Int.self, forKey: .percent)
    }
}

// MARK: - live detection (mirrors detector_server /detect response)

struct Detection: Decodable, Identifiable {
    let id = UUID()
    var label = "object"
    var confidence = 0.0
    var intentHint = "recognize"
    var bbox: [Double] = [0, 0, 0, 0]   // x,y,w,h normalized 0..1 over the frame
    enum K: String, CodingKey { case label, confidence, intent_hint, bbox }
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        label = (try? c.decode(String.self, forKey: .label)) ?? "object"
        confidence = (try? c.decode(Double.self, forKey: .confidence)) ?? 0
        intentHint = (try? c.decode(String.self, forKey: .intent_hint)) ?? "recognize"
        bbox = (try? c.decode([Double].self, forKey: .bbox)) ?? [0, 0, 0, 0]
    }
}
private struct DetectResponse: Decodable { var detections: [Detection] = [] }

enum NetError: LocalizedError {
    case noServer, badURL, http(Int), server(String)
    var errorDescription: String? {
        switch self {
        case .noServer: return "Set the PC brain address in Settings first."
        case .badURL: return "The server address looks invalid."
        case .http(let c): return "Server returned HTTP \(c)."
        case .server(let m): return m
        }
    }
}

// MARK: - client

struct ViewerNet {
    var brain: String
    var detector: String
    static let session = UUID().uuidString

    private func brainBase() throws -> URL {
        let s = brain.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { throw NetError.noServer }
        guard let u = URL(string: s) else { throw NetError.badURL }
        return u
    }

    /// Re-host a server-relative (or absolute-with-internal-host) path onto the brain
    /// base we actually reached — same trick as the existing GenerativeClient.resolve.
    private func resolveBrain(_ path: String) throws -> URL {
        let b = try brainBase()
        var pathOnly = path, query: String? = nil
        if let abs = URLComponents(string: path), abs.scheme != nil { pathOnly = abs.path; query = abs.query }
        else if let qi = path.firstIndex(of: "?") { pathOnly = String(path[..<qi]); query = String(path[path.index(after: qi)...]) }
        guard var comps = URLComponents(url: b, resolvingAgainstBaseURL: false) else { throw NetError.badURL }
        comps.path = pathOnly.hasPrefix("/") ? pathOnly : "/" + pathOnly
        comps.query = query
        guard let out = comps.url else { throw NetError.badURL }
        return out
    }

    private func check(_ resp: URLResponse) throws {
        guard let http = resp as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else { throw NetError.http(http.statusCode) }
    }

    // POST /modular — text prompt -> the modular experience contract.
    func generateModular(text: String) async throws -> ModularExperience {
        var req = URLRequest(url: try brainBase().appendingPathComponent("modular"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 120
        let payload: [String: Any] = ["kind": "text", "text": text, "use_llm": true,
                                      "client": "ios", "session": Self.session]
        req.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try check(resp)
        return try JSONDecoder().decode(ModularExperience.self, from: data)
    }

    func pollJob(_ id: String) async throws -> JobState {
        let url = try brainBase().appendingPathComponent("jobs").appendingPathComponent(id)
        let (data, resp) = try await URLSession.shared.data(from: url)
        try check(resp)
        return try JSONDecoder().decode(JobState.self, from: data)
    }

    /// Poll a Meshy generation job to completion, then download its USDZ to a local
    /// file RealityKit can load. `onProgress` surfaces the 9-stage determinate bar.
    func awaitGeneratedUSDZ(jobId: String,
                            onProgress: @escaping (JobState) -> Void) async throws -> URL {
        let deadline = Date().addingTimeInterval(1200)   // Meshy: minutes
        while true {
            if Date() > deadline { throw NetError.server("Generation timed out.") }
            let s = try await pollJob(jobId)
            onProgress(s)
            if s.status == "done" {
                guard let path = s.usdz_url ?? s.glb_url else { throw NetError.server("Job done but no model.") }
                return try await download(path, name: "gen_\(jobId)")
            }
            if s.status == "error" || s.status == "failed" { throw NetError.server(s.message ?? "Generation failed.") }
            try await Task.sleep(nanoseconds: 1_800_000_000)
        }
    }

    /// Download a brain artifact / library asset to a local .usdz file.
    func download(_ path: String, name: String) async throws -> URL {
        let url = try resolveBrain(path)
        let (tmp, resp) = try await URLSession.shared.download(from: url)
        try check(resp)
        let ext = (path.lowercased().hasSuffix(".glb")) ? "glb" : "usdz"
        let dst = FileManager.default.temporaryDirectory.appendingPathComponent("\(name).\(ext)")
        try? FileManager.default.removeItem(at: dst)
        try FileManager.default.moveItem(at: tmp, to: dst)
        return dst
    }

    /// Resolve an asset's usdzUrl (preferred on iOS) to an absolute brain URL string.
    func assetURLString(_ rel: String) -> String? {
        guard !rel.isEmpty, let u = try? resolveBrain(rel) else { return nil }
        return u.absoluteString
    }

    // POST {detector}/detect — a camera frame JPEG -> Gemini detections.
    func detect(jpeg: Data) async throws -> [Detection] {
        let d = detector.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !d.isEmpty, let base = URL(string: d) else { throw NetError.noServer }
        var req = URLRequest(url: base.appendingPathComponent("detect"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 60
        let payload: [String: Any] = ["imageBase64": jpeg.base64EncodedString(), "mime": "image/jpeg"]
        req.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try check(resp)
        return (try? JSONDecoder().decode(DetectResponse.self, from: data).detections) ?? []
    }
}
