// SessionEvent.swift
// Wire types for the realtime protocol.
// Spec: docs/xr/REALTIME_PROTOCOL.md
//
// ⚠️  When you add an inbound or outbound event type, also:
//   1. Add the new `case` to the `Inbound` or `Outbound` enum below.
//   2. Update docs/xr/REALTIME_PROTOCOL.md §3 or §4.
//   3. Update pipeline/server/spatail_session_server.py:INBOUND_EVENT_TYPES
//      or OUTBOUND_EVENT_TYPES to match.
//   4. Run `npm run sync:check`.

import Foundation

// ───────────────────────────────────────────────────────────────
// Envelope
// ───────────────────────────────────────────────────────────────

public struct SessionEnvelope<Payload: Codable & Sendable>: Codable, Sendable {
    public let type: String
    public let seq: Int
    public let sentAt: String
    public let payload: Payload

    public init(type: String, seq: Int, payload: Payload) {
        self.type = type
        self.seq = seq
        self.sentAt = ISO8601DateFormatter().string(from: Date())
        self.payload = payload
    }
}

// ───────────────────────────────────────────────────────────────
// Inbound — server → iOS  (closed vocab — keep in sync with server)
// ───────────────────────────────────────────────────────────────

public enum InboundEventType: String, Codable, CaseIterable, Sendable {
    case sessionReady          = "session.ready"
    case understandingPartial  = "understanding.partial"
    case assetUrl              = "asset.url"
    case experienceDelta       = "experience.delta"
    case narrationChunk        = "narration.chunk"
    case error                 = "error"
}

public enum ServerEvent: Sendable {
    case sessionReady(SessionReadyPayload)
    case understandingPartial(UnderstandingPartialPayload)
    case assetUrl(AssetUrlPayload)
    case experienceFull(ExperienceFullPayload)
    case experiencePatch(ExperiencePatchPayload)
    case narrationChunk(NarrationChunkPayload)
    case error(ErrorPayload)
}

public struct SessionReadyPayload: Codable, Sendable {
    public let sessionId: String
    public let serverVersion: String
    public let bundleSchemaVersion: String
}

public struct UnderstandingPartialPayload: Codable, Sendable {
    public let stage: String
    public let label: String?
    public let progress: Double?
}

public struct AssetUrlPayload: Codable, Sendable {
    public let bundleId: String
    public let sceneUsdz: URL
    public let heroThumbnail: URL?
    public let byteSize: Int?
    public let etag: String?
}

public struct ExperienceFullPayload: Codable, Sendable {
    public let version: Int
    public let kind: String   // "full"
    public let experience: ExperienceContract
}

public struct ExperiencePatchPayload: Codable, Sendable {
    public let version: Int
    public let kind: String   // "patch"
    public let patches: [AnyCodable]   // RFC 6902 ops; apply via JSONPatcher
}

public struct NarrationChunkPayload: Codable, Sendable {
    public let stepId: String
    public let audioUrl: URL?
    public let durationMs: Int?
}

public struct ErrorPayload: Codable, Sendable {
    public let code: String
    public let message: String
    public let retryAfterMs: Int?
}

// ───────────────────────────────────────────────────────────────
// Outbound — iOS → server
// ───────────────────────────────────────────────────────────────

public enum OutboundEventType: String, Codable, CaseIterable, Sendable {
    case sessionStart     = "session.start"
    case sessionResync    = "session.resync"
    case sessionEnd       = "session.end"
    case userPrompt       = "user.prompt"
    case roomUpdate       = "room.update"
    case poseUpdate       = "pose.update"
    case interactionTap   = "interaction.tap"
}

public struct SessionStartPayload: Codable, Sendable {
    public let client: ClientInfo
    public let capabilities: Capabilities
    public let supportedBundleSchemaVersions: [String]

    public struct ClientInfo: Codable, Sendable {
        public let platform: String
        public let appVersion: String
        public let osVersion: String
    }

    public struct Capabilities: Codable, Sendable {
        public let arkitVersion: String
        public let hasLidar: Bool
        public let supportsRoomCapture: Bool
        public let supportsRealityKit2: Bool
    }
}

public struct UserPromptPayload: Codable, Sendable {
    public let text: String
    public let audioUrl: URL?
    public let context: Context?

    public struct Context: Codable, Sendable {
        public let previousExperienceId: String?
    }
}

public struct RoomUpdatePayload: Codable, Sendable {
    public let version: Int
    public let kind: String          // "delta" | "full"
    public let added: [Plane]?
    public let changed: [Plane]?
    public let removed: [String]?
    public let userPose: Pose?

    public struct Plane: Codable, Sendable {
        public let id: String
        public let kind: String      // "horizontal" | "vertical"
        public let centroid: [Float]
        public let extent: [Float]
        public let normal: [Float]
    }

    public struct Pose: Codable, Sendable {
        public let position: [Float]
        public let forward: [Float]
    }
}

public struct PoseUpdatePayload: Codable, Sendable {
    public let position: [Float]
    public let forward: [Float]
    public let right: [Float]
}

public struct InteractionTapPayload: Codable, Sendable {
    public let elementId: String
    public let tapWorld: [Float]?
}

public struct SessionResyncPayload: Codable, Sendable {
    public let knownVersion: Int
}

public struct SessionEndPayload: Codable, Sendable {
    public let reason: String
}
