import Foundation

/// A tolerant JSON scalar/list value. This is the currency of the whole engine:
/// blackboard entries, component fields, rule arguments, kit params, event payloads
/// are all `Value`s. It mirrors the app's existing `AnyParam` but is mutable,
/// comparable, and round-trippable so rules/objectives can read AND write it.
public enum Value: Codable, Equatable {
    case number(Double)
    case text(String)
    case bool(Bool)
    case list([Value])
    case null

    public var asDouble: Double? {
        switch self {
        case .number(let d): return d
        case .bool(let b): return b ? 1 : 0
        case .text(let s): return Double(s)
        default: return nil
        }
    }
    public var asFloat: Float? { asDouble.map(Float.init) }
    public var asString: String? {
        switch self {
        case .text(let s): return s
        case .number(let d): return d == d.rounded() ? String(Int(d)) : String(d)
        case .bool(let b): return b ? "true" : "false"
        default: return nil
        }
    }
    public var asBool: Bool? {
        switch self {
        case .bool(let b): return b
        case .number(let d): return d != 0
        default: return nil
        }
    }
    public var asList: [Value]? { if case .list(let l) = self { return l }; return nil }

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null; return }
        if let b = try? c.decode(Bool.self)     { self = .bool(b);   return }   // before Double: keep true/false
        if let d = try? c.decode(Double.self)   { self = .number(d); return }
        if let s = try? c.decode(String.self)   { self = .text(s);   return }
        if let l = try? c.decode([Value].self)  { self = .list(l);   return }
        self = .null
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .number(let d): try c.encode(d)
        case .text(let s):   try c.encode(s)
        case .bool(let b):   try c.encode(b)
        case .list(let l):   try c.encode(l)
        case .null:          try c.encodeNil()
        }
    }
}

public extension Dictionary where Key == String, Value == SpatailEngineCore.Value {
    func num(_ k: String, _ d: Double = 0) -> Double { self[k]?.asDouble ?? d }
    func flt(_ k: String, _ d: Float = 0) -> Float { self[k]?.asFloat ?? d }
    func str(_ k: String, _ d: String = "") -> String { self[k]?.asString ?? d }
    func bln(_ k: String, _ d: Bool = false) -> Bool { self[k]?.asBool ?? d }
    func lst(_ k: String) -> [SpatailEngineCore.Value] { self[k]?.asList ?? [] }
}
