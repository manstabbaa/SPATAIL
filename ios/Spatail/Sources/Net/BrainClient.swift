// BrainClient.swift — async/await HTTP client for the PC job server
// (studio/server/job_server.py, LIVE_BRAIN_SPEC §2). Ported from the legacy
// GenerativeClient: same endpoint paths, same Tailscale discipline —
//
//   POST /modular           prompt (or photo) → ModularContract, synchronous
//   GET  /jobs/{id}         Blender/Meshy job progress → JobStatusWire
//   GET  /artifacts/* etc.  asset bytes (GLB/USDZ/referenceobject), cached
//   POST /placement-report  client attribution into the trace (spec §2.2)
//
// Tailscale pattern kept verbatim: the server may hand back ABSOLUTE urls
// built from its own internal host (http://localhost:8787/...). The phone
// can't reach the PC's localhost over the tailnet, so `resolve` keeps only
// path+query and re-hosts onto the base URL that polling already reached.

import Foundation

enum BrainClientError: LocalizedError {
    case badURL
    case http(Int)
    case server(String)
    case timeout

    var errorDescription: String? {
        switch self {
        case .badURL: return "The server address looks invalid."
        case .http(let code): return "Server returned HTTP \(code)."
        case .server(let message): return message
        case .timeout: return "The brain took too long. Try again."
        }
    }
}

final class BrainClient {

    let endpoints: BrainEndpoints

    /// Stable id for THIS running app instance — sent with every request so
    /// the PC binds this phone to its own Blender session/lane (builds from
    /// different phones run concurrently instead of queuing behind each other).
    static let sessionId = UUID().uuidString

    private let session: URLSession

    init(endpoints: BrainEndpoints) {
        self.endpoints = endpoints
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 1200   // GLB downloads over tailnet
        config.waitsForConnectivity = true
        session = URLSession(configuration: config)
    }

    // MARK: - POST /modular (prompt → contract, synchronous server-side build)

    /// Text prompt → the modular experience contract. The server may attach a
    /// `generationJobId` for the asynchronous Blender/Meshy asset build —
    /// poll it with `jobStatus`/`awaitJob`.
    func generateModular(prompt: String) async throws -> ModularContract {
        try await generateModularRaw(prompt: prompt).contract
    }

    /// Same, keeping the raw response bytes — the library persists the server
    /// JSON verbatim (ModularContract is tolerant-Decodable only).
    func generateModularRaw(prompt: String) async throws
        -> (contract: ModularContract, raw: Data) {
        try await postModular([
            "kind": "text", "text": prompt, "use_llm": true,
        ])
    }

    /// Phone photo (JPEG) + optional note/question → the same contract via the
    /// brain's image path (Gemini mechanism brief server-side).
    func generateModular(imageJPEG: Data, note: String = "",
                         question: String = "") async throws -> ModularContract {
        try await postModular([
            "kind": "image", "image": imageJPEG.base64EncodedString(),
            "mime": "image/jpeg", "note": note, "question": question,
            "use_llm": true,
        ]).contract
    }

    private func postModular(_ payload: [String: Any]) async throws
        -> (contract: ModularContract, raw: Data) {
        var body = payload
        body["client"] = "ios"
        body["session"] = Self.sessionId
        var req = URLRequest(url: jobBase().appendingPathComponent("modular"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 120   // synchronous server-side build (engine + agent)
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await session.data(for: req)
        try check(resp, data: data)
        return (try JSONDecoder().decode(ModularContract.self, from: data), data)
    }

    // MARK: - GET /jobs/{id}

    func jobStatus(id: String) async throws -> JobStatusWire {
        let url = jobBase().appendingPathComponent("jobs").appendingPathComponent(id)
        let (data, resp) = try await session.data(from: url)
        try check(resp, data: data)
        return try JSONDecoder().decode(JobStatusWire.self, from: data)
    }

    /// Poll a job to completion. Long default cap — Blender/Meshy authoring
    /// runs minutes, not seconds. Throws `.server` on job error, `.timeout`
    /// past the deadline.
    @discardableResult
    func awaitJob(id: String, pollEvery interval: TimeInterval = 2,
                  timeout: TimeInterval = 1200,
                  onStatus: ((JobStatusWire) -> Void)? = nil) async throws -> JobStatusWire {
        let deadline = Date().addingTimeInterval(timeout)
        while true {
            let status = try await jobStatus(id: id)
            onStatus?(status)
            if status.isDone { return status }
            if status.isFailed {
                throw BrainClientError.server(status.message ?? "Generation failed.")
            }
            if Date() > deadline { throw BrainClientError.timeout }
            try await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
        }
    }

    // MARK: - Asset download (+ local cache)

    /// Download a brain artifact (GLB/USDZ/referenceobject/...) to a stable
    /// local file under Application Support/SpatailAssets, keyed by its
    /// server path — repeat requests hit the cache, never the network.
    func downloadAsset(relativePath: String) async throws -> URL {
        let destination = try cacheURL(for: relativePath)
        if FileManager.default.fileExists(atPath: destination.path) {
            return destination
        }
        let remote = try resolve(relativePath)
        let (tmp, resp) = try await session.download(from: remote)
        try check(resp, data: nil)
        try FileManager.default.createDirectory(
            at: destination.deletingLastPathComponent(),
            withIntermediateDirectories: true)
        try? FileManager.default.removeItem(at: destination)
        try FileManager.default.moveItem(at: tmp, to: destination)
        return destination
    }

    /// Wipe the local asset cache (Settings' "clear cache").
    func clearAssetCache() {
        guard let dir = try? Self.cacheDirectory() else { return }
        try? FileManager.default.removeItem(at: dir)
    }

    /// Synchronous cache probe — the local file URL for a server path IF it has
    /// already been downloaded, else nil. Never touches the network (safe on
    /// main). The SpatailEngine RealityKit backend resolves assets through this
    /// (its `resolveAssetURL` seam is synchronous); EngineHost prefetches the
    /// contract's assets through `downloadAsset` before the engine loads, so by
    /// load time this normally hits.
    static func cachedAssetURL(for path: String) -> URL? {
        guard let dir = try? cacheDirectory() else { return nil }
        let url = dir.appendingPathComponent(cacheFileName(for: path))
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    private func cacheURL(for path: String) throws -> URL {
        try Self.cacheDirectory()
            .appendingPathComponent(Self.cacheFileName(for: path))
    }

    private static func cacheDirectory() throws -> URL {
        let support = try FileManager.default.url(for: .applicationSupportDirectory,
                                                  in: .userDomainMask,
                                                  appropriateFor: nil, create: true)
        return support.appendingPathComponent("SpatailAssets", isDirectory: true)
    }

    /// Flatten a server path into a cache-safe file name, preserving the
    /// extension (RealityKit/ModelIO sniff by extension).
    static func cacheFileName(for path: String) -> String {
        let clean = path.split(separator: "?").first.map(String.init) ?? path
        let flat = clean
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            .replacingOccurrences(of: "/", with: "__")
            .replacingOccurrences(of: "\\", with: "__")
        return flat.isEmpty ? "asset" : flat
    }

    // MARK: - POST /placement-report (spec §2.2)

    /// Fire-and-forget attribution: never throws, never blocks the runtime —
    /// a lost report costs a trace row, not an experience.
    func postPlacementReport(_ report: PlacementReportWire) async {
        do {
            try await sendPlacementReport(report)
        } catch {
            print("[BrainClient] placement report failed: \(error.localizedDescription)")
        }
    }

    /// Throwing variant for callers that want delivery confirmation (tests,
    /// the dev trace screen).
    func sendPlacementReport(_ report: PlacementReportWire) async throws {
        var req = URLRequest(url: jobBase().appendingPathComponent("placement-report"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(report)
        let (data, resp) = try await session.data(for: req)
        try check(resp, data: data)
    }

    // MARK: - URL plumbing (the Tailscale re-host trick)

    private func jobBase() -> URL { endpoints.jobServerURL }

    /// Re-host a server-relative (or absolute-with-internal-host) path onto
    /// the job-server base the phone actually reaches. Keeps path + query,
    /// drops the server's own scheme/host (localhost/127.0.0.1 are unreachable
    /// over the tailnet).
    func resolve(_ path: String) throws -> URL {
        var pathOnly = path
        var query: String?
        if let abs = URLComponents(string: path), abs.scheme != nil {
            pathOnly = abs.path
            query = abs.query
        } else if let qi = path.firstIndex(of: "?") {
            pathOnly = String(path[..<qi])
            query = String(path[path.index(after: qi)...])
        }
        guard var comps = URLComponents(url: jobBase(),
                                        resolvingAgainstBaseURL: false) else {
            throw BrainClientError.badURL
        }
        comps.path = pathOnly.hasPrefix("/") ? pathOnly : "/" + pathOnly
        comps.query = query
        guard let out = comps.url else { throw BrainClientError.badURL }
        return out
    }

    private func check(_ resp: URLResponse, data: Data?) throws {
        guard let http = resp as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            // Surface the server's own error text when it sent JSON {error|detail}.
            if let data,
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let message = (obj["error"] as? String) ?? (obj["detail"] as? String) {
                throw BrainClientError.server("\(message) (HTTP \(http.statusCode))")
            }
            throw BrainClientError.http(http.statusCode)
        }
    }
}
