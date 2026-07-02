"""spatail_cad_build.py — CAD generation stage for usermanualXR.

Runs under the text-to-cad CAD venv (build123d + OpenCascade). Takes a
usermanualXR build plan (authored in cm) and, for every structural part,
generates a build123d generator and runs the ``$cad`` skill's ``scripts/step``
launcher to produce a REAL parametric per-part GLB (+ a STEP for provenance).
Writes a manifest the Blender import stage consumes.

This is the "uses the mechanical-engineering skills to create the parts" half
of the pipeline. The Blender driver then imports each part GLB, scales it to
metres, recentres it, and seats it at the plan ``location`` — replacing the old
primitive boxes with genuine CAD geometry.

Usage (under the CAD venv)::

    python spatail_cad_build.py PLAN.json OUT_DIR [--manifest PATH] [--cad-all]
                                [--step-launcher PATH] [--verbose]

Manifest JSON::

    {
      "assetId": "gen_kallax",
      "out_dir": "...",
      "ok": true,
      "n_ok": 7, "n_failed": 0,
      "parts": {
        "side_left": {"glb": "...abs...", "step": "...", "generator": "...",
                       "shape": "panel", "bbox_mm": [38.0, 390.0, 1470.0]},
        ...
      },
      "failed": {"name": "error string"}
    }
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import spatail_cad_templates as tmpl  # noqa: E402

# Defaults: the vendored text-to-cad CAD skill launcher + venv interpreter.
REPO_ROOT = HERE.parents[1]  # C:/SPATAIL_MAX
DEFAULT_STEP_LAUNCHER = REPO_ROOT / "vendor" / "text-to-cad" / "skills" / "cad" / "scripts" / "step"


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_]", "_", s or "part") or "part"


def _glb_to_blender_mesh(glb_path: Path):
    """Bake a produced part GLB into a Blender-ready mesh payload.

    ``scripts/step`` writes a native **Y-up, metres-magnitude** GLB. We load it
    with trimesh, convert the vertices to Blender's **Z-up** frame
    ``(x, y, z)_blender = (x, -z, y)_gltf`` (the exact inverse of the driver's
    Blender->glTF mapping), recentre to the bounding-box centre so the Blender
    driver can seat it by ``location`` exactly like a primitive, and return the
    arrays + extents (metres).

    Returns ``(verts Nx3 float32, faces Mx3 int32, extents[3])`` or ``None``.
    """
    try:
        import numpy as np
        import trimesh
        mesh = trimesh.load(str(glb_path), force="mesh")
        if mesh is None or len(getattr(mesh, "vertices", [])) == 0:
            return None
        v = np.asarray(mesh.vertices, dtype=np.float64)        # gltf Y-up, metres
        vb = np.column_stack([v[:, 0], -v[:, 2], v[:, 1]])      # -> Blender Z-up
        center = (vb.min(axis=0) + vb.max(axis=0)) * 0.5
        vb = vb - center                                        # origin at centre
        ext = (vb.max(axis=0) - vb.min(axis=0))
        faces = np.asarray(mesh.faces, dtype=np.int32)
        return (vb.astype(np.float32), faces,
                [round(float(x), 5) for x in ext])
    except Exception:
        return None


def _resolve_cad_spec(part: dict, *, cad_all: bool) -> dict | None:
    """Return the cad spec to build this part with, or None to skip (primitive)."""
    explicit = part.get("cad")
    if explicit:
        return dict(explicit)
    if cad_all:
        return tmpl.derive_cad_spec(part)
    return None


def build_plan_cad(plan: dict, out_dir: Path, *, cad_all: bool = True,
                   step_launcher: Path | None = None, verbose: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    step_launcher = Path(step_launcher or DEFAULT_STEP_LAUNCHER)
    if not step_launcher.exists():
        raise FileNotFoundError(f"scripts/step launcher missing at {step_launcher}")

    asset_id = plan.get("assetId", "generated_asset")
    parts_out: dict[str, dict] = {}
    failed: dict[str, str] = {}

    for part in plan.get("parts", []):
        name = part.get("name")
        if not name:
            continue
        cad = _resolve_cad_spec(part, cad_all=cad_all)
        if cad is None:
            continue  # leave as primitive (no GLB → Blender falls back)

        slug = _slug(name)
        gen_path = out_dir / f"{slug}.py"
        glb_path = out_dir / f"{slug}.glb"

        # Bespoke generator authored by an agent takes precedence over templates.
        bespoke = cad.get("generator")
        if bespoke:
            src_path = Path(bespoke)
            if not src_path.is_absolute():
                src_path = (out_dir / bespoke).resolve()
            if not src_path.exists():
                failed[name] = f"bespoke generator not found: {src_path}"
                continue
            gen_path = src_path
        else:
            try:
                source = tmpl.emit_generator(part, cad=cad)
            except Exception as e:  # noqa: BLE001
                failed[name] = f"emit failed: {e}"
                continue
            gen_path.write_text(source, encoding="utf-8")

        # Run the CAD skill's scripts/step to produce the validated GLB (+ STEP).
        cmd = [sys.executable, str(step_launcher), gen_path.name,
               "--glb", glb_path.name]
        if verbose:
            cmd.append("--verbose")
        proc = subprocess.run(cmd, cwd=str(gen_path.parent), capture_output=True,
                              text=True)
        if not glb_path.exists():
            tail = (proc.stderr or proc.stdout or "")[-1200:]
            failed[name] = f"scripts/step produced no GLB (rc={proc.returncode}):\n{tail}"
            continue

        step_path = gen_path.with_suffix(".step")
        entry = {
            "glb": str(glb_path.resolve()),
            "step": str(step_path.resolve()) if step_path.exists() else None,
            "generator": str(gen_path.resolve()),
            "shape": (cad.get("shape") or "box"),
        }

        # Bake the GLB into a Blender-ready Z-up, centred, metres mesh payload.
        payload = _glb_to_blender_mesh(glb_path)
        if payload is not None:
            import numpy as np
            verts, faces, ext = payload
            mesh_path = out_dir / f"{slug}.mesh.npz"
            np.savez_compressed(mesh_path, verts=verts, faces=faces)
            entry["mesh"] = str(mesh_path.resolve())
            entry["bbox_m"] = ext
            entry["n_verts"] = int(verts.shape[0])
            entry["n_faces"] = int(faces.shape[0])
        else:
            entry["mesh"] = None
            entry["bbox_m"] = None

        parts_out[name] = entry
        if verbose:
            print(f"[cad_build] OK {name} -> {glb_path.name} "
                  f"(bbox_m={entry.get('bbox_m')}, faces={entry.get('n_faces')})")

    manifest = {
        "assetId": asset_id,
        "out_dir": str(out_dir.resolve()),
        "ok": len(failed) == 0,
        "n_ok": len(parts_out),
        "n_failed": len(failed),
        "parts": parts_out,
        "failed": failed,
        "_units": "Baked mesh payloads (.npz) are METRES, Blender Z-up, centred on "
                  "origin; the Blender stage imports them at x1.0 and seats by plan "
                  "location. (scripts/step GLBs are metres-magnitude, Y-up.)",
    }
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="spatail_cad_build.py")
    ap.add_argument("plan", help="Build-plan JSON (cm), or a segment JSON containing parts.")
    ap.add_argument("out_dir", help="Directory for per-part generators + GLB/STEP.")
    ap.add_argument("--manifest", help="Where to write the manifest JSON (default: out_dir/cad_manifest.json).")
    ap.add_argument("--cad-all", dest="cad_all", action="store_true", default=True,
                    help="Derive a CAD spec for every part lacking an explicit cad block (default on).")
    ap.add_argument("--no-cad-all", dest="cad_all", action="store_false",
                    help="Only build parts that carry an explicit cad block.")
    ap.add_argument("--step-launcher", help="Path to the $cad scripts/step launcher.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    # Accept either a bare plan or a full segment (both carry `parts`).
    out_dir = Path(args.out_dir)
    manifest_path = Path(args.manifest) if args.manifest else (out_dir / "cad_manifest.json")

    try:
        manifest = build_plan_cad(plan, out_dir, cad_all=args.cad_all,
                                  step_launcher=args.step_launcher, verbose=args.verbose)
    except Exception as e:  # noqa: BLE001
        manifest = {"ok": False, "error": str(e), "trace": traceback.format_exc()}

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"ok": manifest.get("ok"), "n_ok": manifest.get("n_ok"),
                      "n_failed": manifest.get("n_failed"),
                      "manifest": str(manifest_path)}, indent=2))
    return 0 if manifest.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
