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

    private func base() throws -> URL {
        let s = Self.baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { throw GenError.noServer }
        guard let u = URL(string: s) else { throw GenError.badURL }
        return u
    }

    // Resolve a possibly-relative artifact URL against the base.
    private func resolve(_ path: String) throws -> URL {
        if let abs = URL(string: path), abs.scheme != nil { return abs }
        return try base().appendingPathComponent(path.hasPrefix("/") ? String(path.dropFirst()) : path)
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

    private func submit(prompt: String) async throws -> CreateJobResponse {
        var req = URLRequest(url: try base().appendingPathComponent("jobs"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(
            withJSONObject: ["prompt": prompt, "client": "ios"])
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
