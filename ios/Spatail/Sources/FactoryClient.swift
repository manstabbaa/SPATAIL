import Foundation
#if canImport(UIKit)
import UIKit
#endif

// Talks to the PC's AI Asset Factory routes over the SAME Tailscale base the rest
// of the app uses (GenerativeClient.baseURL):
//   GET /factory/index                         -> { assets:[FactoryAsset], count }
//   GET /factory/file/<id>/<file>              -> USDZ / preview.png / GLB bytes
// Re-hosts any returned path onto the base the phone actually reached (mirrors
// GenerativeClient.resolve) so server-internal localhost URLs never leak through.

@MainActor
final class FactoryClient {
    enum FError: LocalizedError {
        case noServer, badURL, http(Int)
        var errorDescription: String? {
            switch self {
            case .noServer: return "Set the PC server address first (open Projects → Server)."
            case .badURL: return "The server address looks invalid."
            case .http(let c): return "Server returned HTTP \(c)."
            }
        }
    }

    private func base() throws -> URL {
        let s = GenerativeClient.baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { throw FError.noServer }
        guard let u = URL(string: s) else { throw FError.badURL }
        return u
    }

    private func resolve(_ path: String) throws -> URL {
        let b = try base()
        var pathOnly = path
        var query: String? = nil
        if let abs = URLComponents(string: path), abs.scheme != nil {
            pathOnly = abs.path; query = abs.query
        } else if let qi = path.firstIndex(of: "?") {
            pathOnly = String(path[..<qi]); query = String(path[path.index(after: qi)...])
        }
        guard var comps = URLComponents(url: b, resolvingAgainstBaseURL: false) else { throw FError.badURL }
        comps.path = pathOnly.hasPrefix("/") ? pathOnly : "/" + pathOnly
        comps.query = query
        guard let out = comps.url else { throw FError.badURL }
        return out
    }

    private func check(_ resp: URLResponse) throws {
        if let h = resp as? HTTPURLResponse, !(200..<300).contains(h.statusCode) {
            throw FError.http(h.statusCode)
        }
    }

    func fetchIndex() async throws -> [FactoryAsset] {
        let url = try resolve("/factory/index")
        let (data, resp) = try await URLSession.shared.data(from: url)
        try check(resp)
        return try JSONDecoder().decode(FactoryIndex.self, from: data).assets
    }

    /// Download the normalized USDZ to a stable, RealityKit-loadable .usdz file.
    func downloadUSDZ(_ asset: FactoryAsset) async throws -> URL {
        guard !asset.usdzUrl.isEmpty else { throw FError.badURL }
        let url = try resolve(asset.usdzUrl)
        let (tmp, resp) = try await URLSession.shared.download(from: url)
        try check(resp)
        let dst = FileManager.default.temporaryDirectory.appendingPathComponent("factory_\(asset.id).usdz")
        try? FileManager.default.removeItem(at: dst)
        try FileManager.default.moveItem(at: tmp, to: dst)
        return dst
    }

    #if canImport(UIKit)
    func previewImage(_ asset: FactoryAsset) async -> UIImage? {
        guard !asset.previewUrl.isEmpty, let url = try? resolve(asset.previewUrl) else { return nil }
        guard let (data, resp) = try? await URLSession.shared.data(from: url) else { return nil }
        if let h = resp as? HTTPURLResponse, !(200..<300).contains(h.statusCode) { return nil }
        return UIImage(data: data)
    }
    #endif
}
