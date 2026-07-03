// JobStatusWire.swift — GET /jobs/{id} on the PC job server
// (studio/server/job_server.py). Ported from the legacy GenerativeClient +
// SpatailViewer ViewerNet job-polling shapes, unified: every field either
// lineage ever read decodes here, tolerantly (a server that doesn't send a
// field yields nil → indeterminate UI, never a decode failure).
//
// Server field names are the wire truth: snake_case artifact urls
// (usdz_url, glb_url, …) + camelCase progress fields (stageIndex, percent).

import Foundation

struct JobStatusWire: Decodable {
    let id: String
    /// Raw server status: "queued" | "running" | "done" | "error" (some
    /// lineages also emit "failed"). Kept verbatim — helpers below interpret.
    let status: String
    let stage: String?
    let message: String?

    // Artifacts (mode: object / Meshy builds)
    let usdzUrl: String?
    let glbUrl: String?
    let metadataUrl: String?

    // Queue + determinate progress (per-phone Blender lane scheduler)
    let queuePosition: Int?
    let stageIndex: Int?
    let stageTotal: Int?
    let percent: Int?

    // Experience jobs (mode == "experience")
    let experienceUrl: String?
    let usdzBase: String?
    let stations: Int?
    let title: String?

    // Representation jobs (mode == "representation")
    let runtimeUrl: String?
    let deliveryUrl: String?
    let progressiveUrl: String?
    let planUrl: String?

    var isDone: Bool { status == "done" }
    var isFailed: Bool { status == "error" || status == "failed" }
    var isFinished: Bool { isDone || isFailed }
    /// Preferred downloadable model path: GLB is the asset format of record,
    /// USDZ the RealityKit-native fallback — take whichever the server built.
    var modelPath: String? { usdzUrl ?? glbUrl }
    /// Human-readable progress line for HUD/status UI.
    var progressText: String { stage ?? status }

    enum K: String, CodingKey {
        case id, status, stage, message
        case usdzUrl = "usdz_url"
        case glbUrl = "glb_url"
        case metadataUrl = "metadata_url"
        case queuePosition, stageIndex, stageTotal, percent
        case experienceUrl = "experience_url"
        case usdzBase = "usdz_base"
        case stations, title
        case runtimeUrl = "runtime_url"
        case deliveryUrl = "delivery_url"
        case progressiveUrl = "progressive_url"
        case planUrl = "plan_url"
    }

    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? ""
        status = (try? c.decode(String.self, forKey: .status)) ?? "queued"
        stage = try? c.decode(String.self, forKey: .stage)
        message = try? c.decode(String.self, forKey: .message)
        usdzUrl = try? c.decode(String.self, forKey: .usdzUrl)
        glbUrl = try? c.decode(String.self, forKey: .glbUrl)
        metadataUrl = try? c.decode(String.self, forKey: .metadataUrl)
        queuePosition = try? c.decode(Int.self, forKey: .queuePosition)
        stageIndex = try? c.decode(Int.self, forKey: .stageIndex)
        stageTotal = try? c.decode(Int.self, forKey: .stageTotal)
        percent = try? c.decode(Int.self, forKey: .percent)
        experienceUrl = try? c.decode(String.self, forKey: .experienceUrl)
        usdzBase = try? c.decode(String.self, forKey: .usdzBase)
        stations = try? c.decode(Int.self, forKey: .stations)
        title = try? c.decode(String.self, forKey: .title)
        runtimeUrl = try? c.decode(String.self, forKey: .runtimeUrl)
        deliveryUrl = try? c.decode(String.self, forKey: .deliveryUrl)
        progressiveUrl = try? c.decode(String.self, forKey: .progressiveUrl)
        planUrl = try? c.decode(String.self, forKey: .planUrl)
    }
}
