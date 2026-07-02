import XCTest
import SpatailEngineCore

/// Records every command the core emits, so tests can assert on what the platform
/// would render. This is also the proof that the core runs fully headless — no
/// RealityKit, no device.
final class MockBackend: SceneBackend {
    var commands: [BackendCommand] = []
    func apply(_ command: BackendCommand) { commands.append(command) }

    var createdCount: Int { commands.filter { if case .createVisual = $0 { return true }; return false }.count }
    var removedCount: Int { commands.filter { if case .removeVisual = $0 { return true }; return false }.count }
    var spoken: [String] { commands.compactMap { if case .speak(let t) = $0 { return t }; return nil } }
}

final class EngineTests: XCTestCase {

    // The whole thesis in one test: ONE engine, a SHOOTER genre, expressed purely as
    // data (entities + components + the kit's authored rules/objectives), runs to a
    // win — fire → projectile → collision → damage → death → score → objective → win.
    func testShooterRunsToWin() throws {
        let json = """
        {
          "schemaVersion": "0.8-spatail-engine",
          "id": "test_shooter",
          "title": "Target Range",
          "genre": "shooter",
          "params": { "targetScore": 1, "points": 1 },
          "entities": [
            { "id": "target1", "primitive": "box", "size": [0.3,0.3,0.3], "tags": ["target"],
              "transform": { "position": [0,0,-2] },
              "components": [ { "type": "health", "fields": { "hp": 1, "max": 1 } } ] }
          ],
          "templates": [
            { "id": "projectile", "primitive": "sphere", "size": [0.1,0.1,0.1], "tags": ["projectile"],
              "components": [ { "type": "projectile", "fields": { "damage": 1 } },
                              { "type": "lifetime", "fields": { "ttl": 3 } } ],
              "physics": { "mode": "dynamic", "body": "bouncy", "mass": 0.2, "collider": "sphere" } }
          ]
        }
        """
        let c = try ExperienceContract.decode(Data(json.utf8))
        let mock = MockBackend()
        let engine = SpatailEngine(backend: mock)
        engine.load(c)

        // initial scene rendered
        XCTAssertEqual(mock.createdCount, 1, "the target should have been created")
        let target = engine.world.query(tag: "target").first
        XCTAssertNotNil(target)

        // fire: the brain's kit rule spawns a physics projectile down the camera ray
        engine.submit(EngineEvent(Ev.fire, payload: [
            "origin":  .list([.number(0), .number(0), .number(0)]),
            "impulse": .list([.number(0), .number(0), .number(-5)])
        ]))
        let projectile = engine.world.query(tag: "projectile").first
        XCTAssertNotNil(projectile, "fire rule should have spawned a projectile")

        // collision: projectile strikes the target (backend would emit this)
        engine.submit(EngineEvent(Ev.collision, entity: projectile!.id, other: target!.id))

        // let health → death → score → objective → win settle over a few frames
        for _ in 0..<4 { engine.tick(1.0 / 60.0) }

        XCTAssertEqual(engine.world.get(BB.score)?.asDouble, 1, "killing the target scores a point")
        XCTAssertEqual(engine.world.get(BB.outcome)?.asString, "win", "reaching the target score wins")
        XCTAssertNil(engine.world.entities[target!.id], "the target should have been despawned")
        XCTAssertGreaterThanOrEqual(mock.removedCount, 1)
    }

    // The SAME engine, an EXPLAINER genre, runs today's step-sequence behavior.
    func testExplainerAdvancesSteps() throws {
        let json = """
        {
          "id": "test_explainer",
          "title": "Newton I",
          "genre": "explainer",
          "entities": [
            { "id": "hero", "primitive": "box", "tags": ["hero"], "transform": { "position": [0,0,-1] } }
          ],
          "steps": [
            { "id": "s0", "title": "Intro",  "narration": "An object at rest...", "focus": "hero", "clip": "demo" },
            { "id": "s1", "title": "Detail", "focus": "hero", "effect": "emissive", "target": "core" },
            { "id": "s2", "title": "Wrap" }
          ]
        }
        """
        let c = try ExperienceContract.decode(Data(json.utf8))
        let mock = MockBackend()
        let engine = SpatailEngine(backend: mock)
        engine.load(c)

        XCTAssertEqual(engine.world.get(BB.stepIndex)?.asDouble, 0)
        XCTAssertEqual(engine.world.get(BB.stepCount)?.asDouble, 3)
        XCTAssertTrue(mock.spoken.contains("An object at rest..."), "step 0 narration should fire on load")

        engine.submit(EngineEvent(Ev.tap))      // 0 -> 1
        XCTAssertEqual(engine.world.get(BB.stepIndex)?.asDouble, 1)
        engine.submit(EngineEvent(Ev.tap))      // 1 -> 2
        XCTAssertEqual(engine.world.get(BB.stepIndex)?.asDouble, 2)
        engine.submit(EngineEvent(Ev.tap))      // 2 -> end
        XCTAssertEqual(engine.world.get("sequence.atEnd")?.asBool, true)
    }

    // Contract-authored rules + objectives (no kit) drive behavior: tap a coin to
    // collect it, collect 2 to win. Proves "rules/laws/objectives as data".
    func testContractAuthoredRulesAndObjective() throws {
        let json = """
        {
          "id": "test_collect", "title": "Collect", "genre": "explainer",
          "systems": ["rule", "objective"],
          "entities": [
            { "id": "coinA", "primitive": "sphere", "tags": ["coin"] },
            { "id": "coinB", "primitive": "sphere", "tags": ["coin"] }
          ],
          "rules": [
            { "id": "collect", "when": { "event": "input.tap", "target": "coin" },
              "do": [ { "kind": "addScore", "args": { "key": "score", "amount": 1 } },
                      { "kind": "despawn", "args": { "target": "self" } } ] }
          ],
          "objectives": [
            { "id": "collect_all", "title": "Collect 2",
              "success": [ { "lhs": "blackboard.score", "op": "gte", "rhs": 2 } ],
              "onComplete": [] }
          ]
        }
        """
        let c = try ExperienceContract.decode(Data(json.utf8))
        let engine = SpatailEngine(backend: MockBackend())
        engine.load(c)

        let coins = engine.world.query(tag: "coin")
        XCTAssertEqual(coins.count, 2)
        engine.submit(EngineEvent(Ev.tap, entity: coins[0].id))
        engine.tick(0.016)
        engine.submit(EngineEvent(Ev.tap, entity: coins[1].id))
        for _ in 0..<2 { engine.tick(0.016) }

        XCTAssertEqual(engine.world.get(BB.score)?.asDouble, 2)
        XCTAssertEqual(engine.world.get(BB.objectivesAllDone)?.asBool, true)
    }
}
