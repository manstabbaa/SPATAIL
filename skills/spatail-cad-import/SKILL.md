---
name: spatail-cad-import
description: Import real parametric CAD parts (build123d / OpenCascade, via the earthtojake text-to-cad skill) into a Blender build at the correct scale, orientation, and placement. Bridges the two-Python-version gap — build123d runs only in the CAD venv (3.11), Blender ships 3.13 and cannot import it — by baking each CAD part to a small Z-up / metres / origin-centred mesh payload (.npz) in the venv stage, then loading that payload into Blender with from_pydata (no ops, no transforms, safe in a non-active scene). Parts seat by plan `location` exactly like primitives, so CAD geometry is a drop-in replacement for primitive boxes. Use when a build plan's parts should be genuine machined geometry (fillets, tubes, holes, brackets) instead of primitive blocks.
when_to_use: When the generative usermanualXR path should build parts as real CAD instead of primitives — i.e. whenever spatail-model-from-manual would emit a primitive box/cylinder but the part deserves accurate geometry. Also when hand-importing a one-off build123d/STEP part into a Blender asset at the right size and orientation. Pairs with spatail-model-from-manual (the build half) and the CAD-from-manual agent team (analyst → cad-modeler → blender-integrator).
---

# CAD → Blender Import (real parametric parts into the build)

This is the **"bring it into Blender and scale it properly — right orientation and
size"** half of the real-CAD path. The mechanical-engineering CAD skill
(earthtojake's `text-to-cad`, vendored at `vendor/text-to-cad`) models each part as
genuine build123d / OpenCascade geometry; this skill gets that geometry into the
Blender build seated correctly, then [[spatail-model-from-manual]] exports + registers
the asset and the walkthrough plays it.

## Why two stages (the hard constraint)

`build123d` (and its `cadquery-ocp` / OpenCascade backend) installs **only in the CAD
venv — Python 3.11** (`vendor/text-to-cad/.venv`). **Blender 5.1 ships Python 3.13** and
**cannot import build123d**. So the geometry cannot be generated inside Blender. The
pipeline splits in two, communicating through a tiny on-disk payload:

```
┌─ CAD venv (3.11) ───────────────────────────┐   ┌─ Blender (3.13, headless) ───────────────┐
│ plan part → build123d generator → scripts/step │   │ read manifest → load .npz per part        │
│ → part GLB (Y-up, metres) → bake to .npz mesh  │ → │ → from_pydata mesh → seat at plan location │
│   (Z-up, metres, origin-centred)               │   │ → export meters/Y-up GLB + registry        │
└────────────────────────────────────────────────┘   └────────────────────────────────────────────┘
   pipeline/cad/spatail_cad_build.py                     pipeline/blender/spatail_model_from_primitives.py
```

The `.npz` payload is the contract between the two Pythons. It carries only `verts`
(Nx3 float32) + `faces` (Mx3 int32) — no library dependency, so Blender's interpreter
loads it with plain numpy.

## Units & orientation — the round-trip that makes size/orientation correct

This is the part that's easy to get wrong; these facts are **proven**, not assumed.

- The plan is authored in **centimetres**; the Blender driver scales the *plan* cm→m.
- `scripts/step --glb` exports a **Y-up, METRES-magnitude** GLB (build123d authors in
  mm = plan cm × 10, and the exporter divides by 1000). It is **not** mm-magnitude —
  an earlier assumption that it was mm (and needed ×0.001) was wrong and was corrected.
- The driver's forward map is **Blender Z-up `(x,y,z)` → glTF `(x, z, −y)`**. The CAD
  bake applies the exact **inverse**: a glTF vertex `(x, y, z)` → Blender `(x, −z, y)`.
- After converting axes, the bake **recentres to the bounding-box centre** so the part's
  origin is at its centroid — exactly like a primitive — and the Blender driver can seat
  it by the plan `location` with no extra offset.
- Net result: the part imports at **scale ×1.0** (NOT ×0.001), in the original
  orientation, at the right size. A KALLAX built this way measures **0.42 × 0.39 × 1.47 m**
  = its 42 × 39 × 147 cm spec, to the centimetre.

```python
# the inverse-axis convert + recentre (in the CAD venv stage, trimesh + numpy):
v  = np.asarray(mesh.vertices, dtype=np.float64)     # glTF Y-up, metres
vb = np.column_stack([v[:, 0], -v[:, 2], v[:, 1]])   # -> Blender Z-up
vb = vb - (vb.min(0) + vb.max(0)) * 0.5              # origin at centroid
```

## The mesh-payload contract (manifest + .npz)

`spatail_cad_build.py` writes one `cad_manifest.json` plus a `<slug>.mesh.npz` per part:

```json
{ "assetId": "gen_kallax", "ok": true, "n_ok": 7, "n_failed": 0,
  "parts": {
    "side_left": { "glb": "...abs.glb", "step": "...abs.step",
                   "generator": "...abs.py", "shape": "panel",
                   "mesh": "...abs/side_left.mesh.npz",
                   "bbox_m": [0.038, 0.39, 1.47], "n_verts": 1234, "n_faces": 2456 }
  },
  "failed": {} }
```

A part is imported as CAD **iff** its manifest entry has a `mesh` path that exists on
disk. Anything missing (or that fails to load) **falls back to its primitive** — the
import is strictly additive, so a partial or absent manifest never breaks a build.

## Import (Blender side) — already wired

`build_from_plan` takes an optional `cad_manifest` (dict or path). Per part it prefers
the baked CAD payload and falls back to the primitive:

```python
# pipeline/blender/spatail_model_from_primitives.py
res = mp.build_from_plan(plan_m, make_active=False, cad_manifest=cad_manifest)
# res["n_cad"] == how many parts came in as real CAD; registry["_n_cad_parts"] mirrors it.
```

The loader (`_mesh_from_cad_payload`) builds the datablock with **`mesh.from_pydata`**,
then `validate()` + smooth shading. No `bpy.ops`, no transforms, no selection/active
juggling — so it is safe in a **non-active dedicated scene** (`make_active=False`), and
the user's open scene is never touched. Smooth shading is set so the eased CAD edges
(fillets, cylinder walls) read correctly instead of faceted-flat.

## Generate (CAD venv side) — run the mechanical skill

```bash
# under the CAD venv (3.11); --cad-all derives a CAD spec for every plan part
vendor/text-to-cad/.venv/Scripts/python.exe pipeline/cad/spatail_cad_build.py \
    PLAN.json  engine/cad_parts/<assetId>  --manifest .../cad_manifest.json --cad-all
```

Each part becomes a build123d generator (`<slug>.py` exposing `gen_step()`), run through
the `$cad` skill's `scripts/step` launcher to produce a validated GLB (+ STEP for
provenance), then baked to the `.npz`. Generators come from `spatail_cad_templates.py`
(`derive_cad_spec` maps primitive/role/size → panel / bar / dowel / tube / l-bracket /
box, with default edge fillets and optional holes), unless a part carries an explicit
`cad.generator` path authored by an agent — that bespoke generator wins.

## Automated path (one call — generate + import together)

The whole thing is wired behind the normal generative bridge. No manual stitching:

```python
from engineexplainer.intelligence import generative_bridge as gb
out = gb.build_and_register(segment)   # CAD pre-stage → headless Blender build → register
# out["result"]["n_cad"] parts came in as real CAD.
```

`generative_bridge.build_asset_from_plan` calls `generate_cad_manifest(plan, ...)` first
(best-effort: gated by `ENGINEEXPLAINER_USE_CAD`, degrades to primitives if the venv or
toolchain is missing), then passes `cad_manifest` into the Blender build spec. The driver
(`spatail_build_from_plan_driver.py`) reads `spec["cad_manifest"]` and forwards it to
`build_from_plan`. Env knobs:

- `ENGINEEXPLAINER_USE_CAD` — `0/false/no` disables the CAD stage (pure primitives).
- `ENGINEEXPLAINER_CAD_PYTHON` — path to the CAD venv interpreter (default
  `vendor/text-to-cad/.venv/Scripts/python.exe`).

## Verify (proven)

```bash
# tri count + extents of any GLB (trimesh):
python C:/tmp/check_glb.py engine/gen_kallax.glb
#   geometries=7 tris=19572 extents_m=[0.42, 1.47, 0.39]   ← real CAD
# primitive baseline of the same plan is 84 tris, identical extents.
```

A CAD build and a primitive build of the same plan have **identical extents** (same size
+ placement) but the CAD build has orders of magnitude more triangles — that's the
filleted OpenCascade geometry. Check `registry["_n_cad_parts"]` and per-part
`parts[name]["cad"] == true` to confirm which parts came in as real CAD.

## Anti-goals

- ❌ Import build123d *inside* Blender — its Python (3.13) can't load it. Always go
  through the `.npz` payload baked by the 3.11 venv stage.
- ❌ Scale CAD payloads by ×0.001 — they are already metres. The plan cm→m scaling is for
  *primitive* parts; CAD payloads arrive in metres and import at ×1.0.
- ❌ Re-orient or re-centre on the Blender side — the bake already converted axes
  (glTF→Z-up) and centred the origin. Just seat by plan `location`.
- ❌ Use `bpy.ops`/import-operators for the payload — `from_pydata` is ops-free and safe
  in a non-active scene; operators would need the scene active and could disturb the
  user's open scene.
- ❌ Hard-fail when the CAD toolchain is missing — the import is additive; absent/failed
  parts fall back to primitives so a build always completes.
- ❌ Model fine surface detail here — this skill places machined CAD bases; refine with
  [[spatail-mesh-select]] before export if needed.
