---
name: spatail-reassemble
description: Rebuild the assembled engine from per-part measurements + classification kinematic graph. Empty scene start — place crank assembly at origin, throws at calculated Z + journal phase, pistons on bank-bore lines passing through the crank axis, rods with origins at small-ring centres parented to pistons, DT constraints to journal targets. This is the "blueprint assembly" step that makes slider-crank animation work exactly.
when_to_use: After spatail-measure-per-part has produced per-part JSONs. The key contract: every piston ends up on a line through the crank axis at the bank's V-angle, so the exact slider-crank formula `r·cosθ + √(L² − r²·sin²θ)` produces zero rod-length variance during animation.
---

# Reassemble Skill

## What "rebuild from blueprint" means

The OBJ-authored layout has pistons scattered at arbitrary offsets from where they should kinematically sit. Slider-crank requires the piston to slide along a line passing through the crank axis (or with a known constant offset). With OBJ positions, the formula can't keep |piston − journal| = c2c throughout the cycle — gives the visible "swing through wrong centres" we saw at frame 30.

Reassemble fixes this by:
1. Starting with no preconceptions about where parts "are"
2. Reading the kinematic blueprint (classification.json: which parts go where)
3. Reading per-part measurements (per-part JSONs: ring positions, journal positions, c2c)
4. Placing each part by formula

## The blueprint

For a V engine:

```
                              ┌──────────────────────┐
                              │  crank_assembly      │  empty at world origin
                              │  (rotation pivot)    │
                              └──────────┬───────────┘
                                         │
       ┌─────────────────────────────────┼─────────────────────────────────┐
       │                                 │                                 │
       ▼                                 ▼                                 ▼
  throw_1 at Z=Z1          throw_2 at Z=Z2  ...      throw_N at Z=ZN
  (origin at axis,          (origin at axis,          (origin at axis,
   journal offset by         journal at phase φ_2)     journal at phase φ_N)
   throw_radius at phase φ_1)
       │
       ├─→ journal_target_1_A   ┌─ piston_1_A on bank-A bore line at distance L from axis
       │   (parented to throw)  │  → rod_1_A parented to piston, origin at small-ring,
       │                        │     DT aimed at journal_target_1_A
       │                        │
       └─→ journal_target_1_B   └─ piston_1_B on bank-B bore line, mirror layout
           (shared throw)
```

**Bank-bore line definition**: each bank has a bore direction (unit vector in the plane perpendicular to crank axis, tilted at the bank's half-V-angle from "up"). Every piston in that bank slides along its own bore line. Each bore line PASSES THROUGH the crank axis at a specific Z. Pistons in different cyls share a bank-bore direction but sit at different Z.

## Contract

**Input**:
- `<asset>.classification.json` — kinematic groups (cyl × bank → piston/rod/pin/throw)
- `<asset>/measurements/<part>.measurements.json` — per-part fits
- `<asset>/measurements/_index.json` — part roster

**Output**:
- Scene in assembled state — crank at origin, parts placed by formula
- Each piston has `spatail_slot` with: cyl, bank, bore_axis (passing through axis), c2c (from rod's measurement), journal_world, axis_origin
- Each rod has origin at small-ring centre (from measurement), parented to piston, DT to journal_target
- Returns dict with bank V-angle, per-cyl placement log, any blueprint constraints that couldn't be satisfied

## Bank-bore direction policy

If classification provides explicit per-bank bore axes → use those.
Else, derive from the average of (piston − journal) vectors per bank, projected perpendicular to crank axis, normalized, then **mirrored across the +Y axis** to enforce V-symmetry.

For the V10 this came out to about 80° V (≈ ±40° from +Y in the plane).

## Anti-goals

- ❌ Use OBJ piston positions for placement. Reassemble respects measurements, not authored positions.
- ❌ Run animation. That's the next skill.
- ❌ Re-measure anything. Read JSON, place, done.
