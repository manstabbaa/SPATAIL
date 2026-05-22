// PrimsIndex.swift
// USD prim path ↔ contract element id lookup.
// Spec: docs/xr/IOS_BUNDLE_SPEC.md §4

import Foundation

public struct PrimsIndex: Codable, Sendable {
    public let primToElement: [String: String]
    public let elementToPrim: [String: String]

    // Note: the bundle ships these as arrays of [String, String] tuples
    // when JSON-encoded by Python (the dict ordering is preserved). Decode
    // tolerantly: support both [[k,v]] and {k:v} layouts.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.primToElement = try Self.decodeMap(c, .primToElement)
        self.elementToPrim = try Self.decodeMap(c, .elementToPrim)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(primToElement, forKey: .primToElement)
        try c.encode(elementToPrim, forKey: .elementToPrim)
    }

    private enum CodingKeys: String, CodingKey {
        case primToElement, elementToPrim
    }

    private static func decodeMap(
        _ c: KeyedDecodingContainer<CodingKeys>,
        _ key: CodingKeys
    ) throws -> [String: String] {
        if let dict = try? c.decode([String: String].self, forKey: key) {
            return dict
        }
        if let pairs = try? c.decode([[String]].self, forKey: key) {
            var out: [String: String] = [:]
            for p in pairs where p.count == 2 { out[p[0]] = p[1] }
            return out
        }
        return [:]
    }
}
