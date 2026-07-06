import SwiftUI

// SettingsStore — @AppStorage-backed persistence for the PC brain endpoints.
//
// Defaults use the Tailscale MagicDNS short name "spatail-pc": no real tailnet
// hostname is committed anywhere in the repo (the legacy GenerativeClient
// persisted a user-entered base URL — example "mansourspc.tailnet-xxxx.ts.net:8787" —
// and SPATAILMobileAR's LiveVisionModel defaulted to a LAN IP). The user types the
// real address once in Settings; values entered in the legacy apps are migrated
// from their old UserDefaults keys on first run.

@MainActor
final class SettingsStore: ObservableObject {

    // MARK: Defaults

    /// Job server (HTTP): /modular, /jobs, /assets, /placement-report — spec §2.
    static let defaultJobServerURL = "http://spatail-pc:8788"
    /// Vision socket (WS): binary JPEG frames up, identification/delta JSON down — spec §1.
    static let defaultVisionSocketURL = "ws://spatail-pc:8798/v1/vision"

    // Persisted keys (new canon) + the legacy keys we migrate from.
    static let jobServerKey = "spatail.brain.jobServerURL"
    static let visionSocketKey = "spatail.brain.visionSocketURL"
    private static let legacyJobServerKey = "spatail.gen.baseURL"     // legacy GenerativeClient
    private static let legacyVisionHostKey = "spatail.visionHost"    // SPATAILMobileAR LiveVisionModel

    // MARK: Persisted fields
    //
    // @AppStorage inside an ObservableObject persists fine but does not publish
    // on its own — the willSet hooks forward every change to objectWillChange so
    // SwiftUI bindings ($settings.jobServerURLString) stay live.

    @AppStorage(SettingsStore.jobServerKey)
    var jobServerURLString: String = SettingsStore.defaultJobServerURL {
        willSet { objectWillChange.send() }
    }

    @AppStorage(SettingsStore.visionSocketKey)
    var visionSocketURLString: String = SettingsStore.defaultVisionSocketURL {
        willSet { objectWillChange.send() }
    }

    /// RoomPlan furniture entities (v3 §2) — Apple's furniture model sharing the
    /// AR session. Default ON where supported; the runtime degradation watch
    /// stops it (and notes it) if the shared session loses smoothed depth.
    @AppStorage("spatail.perception.roomplan")
    var roomPlanEnabled: Bool = true {
        willSet { objectWillChange.send() }
    }

    init() {
        migrateLegacyKeysIfNeeded()
    }

    // MARK: Endpoints

    /// Validated endpoints, or nil when either URL is unusable.
    var endpoints: BrainEndpoints? {
        guard let job = Self.normalizedURL(jobServerURLString,
                                           defaultScheme: "http",
                                           allowedSchemes: ["http", "https"]),
              let vision = Self.normalizedURL(visionSocketURLString,
                                              defaultScheme: "ws",
                                              allowedSchemes: ["ws", "wss"])
        else { return nil }
        return BrainEndpoints(jobServerURL: job, visionSocketURL: vision)
    }

    /// Human-readable validation problem for the Settings UI (nil when valid).
    var endpointsProblem: String? {
        if Self.normalizedURL(jobServerURLString, defaultScheme: "http",
                              allowedSchemes: ["http", "https"]) == nil {
            return "Job server address looks invalid — expected http://host:8788"
        }
        if Self.normalizedURL(visionSocketURLString, defaultScheme: "ws",
                              allowedSchemes: ["ws", "wss"]) == nil {
            return "Vision socket address looks invalid — expected ws://host:8798/v1/vision"
        }
        return nil
    }

    func resetToDefaults() {
        jobServerURLString = Self.defaultJobServerURL
        visionSocketURLString = Self.defaultVisionSocketURL
    }

    /// One-field convenience: point BOTH endpoints at a PC hostname or IP
    /// (Tailscale MagicDNS name like "mansourspc", or a LAN IP) on the
    /// standard ports. Scheme prefixes and stray slashes are forgiven.
    func applyHost(_ host: String) {
        var h = host.trimmingCharacters(in: .whitespacesAndNewlines)
        for prefix in ["http://", "https://", "ws://", "wss://"] where h.hasPrefix(prefix) {
            h = String(h.dropFirst(prefix.count))
        }
        h = h.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
        if let colon = h.firstIndex(of: ":") { h = String(h[..<colon]) }
        guard !h.isEmpty else { return }
        jobServerURLString = "http://\(h):8788"
        visionSocketURLString = "ws://\(h):8798/v1/vision"
    }

    // MARK: Normalization

    /// Trim, add the scheme when the user typed a bare host, require a host,
    /// reject wrong schemes (an http URL in the vision field is a config error,
    /// not something to silently accept).
    static func normalizedURL(_ raw: String, defaultScheme: String,
                              allowedSchemes: Set<String>) -> URL? {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return nil }
        if !s.contains("://") { s = "\(defaultScheme)://\(s)" }
        guard let url = URL(string: s),
              let scheme = url.scheme?.lowercased(),
              allowedSchemes.contains(scheme),
              url.host != nil
        else { return nil }
        return url
    }

    // MARK: Legacy migration

    /// One-shot: if the new keys were never written but a legacy app configured
    /// its endpoint, adopt it so the user doesn't retype their tailnet address.
    private func migrateLegacyKeysIfNeeded() {
        let defaults = UserDefaults.standard
        if defaults.object(forKey: Self.jobServerKey) == nil,
           let legacy = defaults.string(forKey: Self.legacyJobServerKey),
           !legacy.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            jobServerURLString = legacy
        }
        if defaults.object(forKey: Self.visionSocketKey) == nil,
           let host = defaults.string(forKey: Self.legacyVisionHostKey),
           !host.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            // Legacy stored "host:8798" (no scheme/path) — rebuild the full WS URL.
            visionSocketURLString = host.contains("://") ? host : "ws://\(host)/v1/vision"
        }
    }
}
