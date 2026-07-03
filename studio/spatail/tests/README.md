# Placement solver parity fixtures

Golden fixtures for the SPATAIL Placement Design System solver. They are the
**contract both solvers must satisfy**:

- PC / authoring: `python3 run_parity.py` solves each fixture with
  `studio/spatail/placement_solver.py` and asserts within tolerance.
- Device: a Swift XCTest parses the **same JSON** and runs
  `PlacementSolver.solve` (`ios/.../PlacementSolver.swift`). Green on both
  sides means what authoring previews is what the phone places.

Frame everywhere: metres, +Y up, user near the origin looking **-Z**.

## Fixture schema (`fixtures/*.json`)

```jsonc
{
  "schema": "spatail-placement-parity/1",
  "name": "table_arc_trio",            // = file stem
  "description": "what this fixture locks down",

  "room": {                             // serialized RoomModel (room_model.py)
    "schema": "spatail-room-model/1",
    "surfaces": [{
      "cls": "table",                   // floor|table|wall|ceiling|seat|shelf|counter|unknown
      "center_m": [x, y, z],
      "size_m": [w, d],                 // extent along the surface's local (u, v)
      "normal": [x, y, z],
      "yaw_rad": 0.0,
      "height_m": 0.74,                 // height above floor
      "confidence": 1.0
    }],
    "obstacles": [{
      "bbox_min_m": [x, y, z], "bbox_max_m": [x, y, z], "category": "chair"
    }],
    "free_floor_m": [],                 // [[x, z], ...] polygon (unused by the solver)
    "user": {"position_m": [x, y, z], "forward": [x, y, z], "eye_height_m": 1.45},
    "bounds_m": [w, d, ceiling],
    "source": "fixture"
  },

  "assets": [{                          // solver asset requirements, hero first not required
    "id": "sun",
    "footprint": [w, d, h],             // real-world metres; only [0] (width) packs the arc
    "role": "primary_object",           // primary_object|comparison_object|part|...
    "realScaleBaked": false             // GLB already metric -> uniform scale pinned to 1.0
  }],

  "intent": {
    "anchorPreference": "table",        // table|floor|free
    "scaleMode": "dynamic",             // dynamic|real  (real -> floor anchor, scale 1.0)
    "primary": "sun",                   // hero asset id (centred, nearest)
    "coverage": 0.8                     // usable fraction of surface width, clamped [0.2, 0.95]
  },

  "expect": {
    "anchor": "table",                  // plan.anchor: table|floor
    "scaleVariant": "tabletop",         // plan.scaleVariant: tabletop|real
    "tolerances": {                     // absolute tolerances for the numeric fields
      "position_m": 0.005, "yaw_rad": 0.005, "scale": 0.005
    },
    "placements": [{                    // matched BY assetId, order-independent;
      "assetId": "sun",                 // count must equal the plan's placement count
      "position_m": [x, y, z],          // within tolerances.position_m per component
      "yaw_rad": 0.0,                   // within tolerances.yaw_rad
      "scale": 1.0,                     // within tolerances.scale
      "anchor": "table",                // exact (optional)
      "zone": "hero",                   // exact (optional): hero|secondary|peripheral
      "fits": true                      // exact
    }],
    "diagnostics": {"coverage": 0.8}    // OPTIONAL, PYTHON-ONLY: subset-matched against
                                        // plan.diagnostics. The Swift Plan intentionally
                                        // has no diagnostics block — XCTest skips this key.
  }
}
```

## Swift field mapping (for the XCTest decoder)

| fixture JSON                          | Swift                                             |
|---------------------------------------|---------------------------------------------------|
| `room.surfaces[].cls/center_m/size_m/normal/yaw_rad/height_m/confidence` | `RoomModel.Surface(cls:center:size:normal:yaw:height:confidence:)` |
| `room.obstacles[].bbox_min_m/bbox_max_m/category` | `RoomModel.Obstacle(bboxMin:bboxMax:category:)` |
| `room.user.position_m/forward/eye_height_m` | `RoomModel.user` tuple |
| `room.bounds_m`                        | `RoomModel.bounds`                                |
| `assets[].id/footprint/role/realScaleBaked` | `PlacementSolver.AssetReq`                    |
| `intent.anchorPreference/scaleMode/primary/coverage` | `PlacementSolver.solve(room:assets:anchorPreference:scaleMode:primary:coverage:)` |
| `expect.anchor/scaleVariant`           | `Plan.anchor` / `Plan.scaleVariant`               |
| `expect.placements[]`                  | `Plan.placements[]` (`position_m`→`position`, `yaw_rad`→`yaw`) |

Caveats the fixtures are deliberately written around:

- **One table per room.** Python `best_table()` picks the largest table; Swift
  `bestTable()` scores near + in-front with plausibility gates. With a single
  plausible table both pick the same surface; multi-table tiebreak parity is
  not part of this contract.
- `reason` strings and the `diagnostics` block are the Python solver's
  canonical richness (kept per the Live Brain spec); Swift dropped them, so
  fixtures only require that Python emits a non-empty `reason`.

## What each fixture locks down

| fixture               | behavior                                                        |
|-----------------------|-----------------------------------------------------------------|
| `table_arc_trio`      | comfort arc geometry + floor furniture filtered from a table placement's keep-out |
| `floor_real_scale`    | `scaleMode "real"` → floor anchor, scale 1.0, floor obstacles stay live |
| `baked_hero_pin`      | `realScaleBaked` hero pins the uniform scale to 1.0 (no coverage-shrink) |
| `coverage_tight`      | `coverage` parameter shrinks the fit scale; default 0.8 preserved elsewhere |
| `obstacle_on_table`   | an obstacle resting ON the chosen surface blocks its slot (`fits: false`) |

## Adding a fixture

1. Write `room` / `assets` / `intent` by hand (keep it minimal + human-checkable).
2. Run the solver once, verify the output is RIGHT (not just deterministic),
   then bake it into `expect` using the 4-decimal values the plan serializes.
3. `python3 run_parity.py <name>` must pass, and the Swift XCTest must pick the
   file up automatically (it globs the same `fixtures/` directory).
