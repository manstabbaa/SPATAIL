"""importers.py — import a raw AI-generated asset into Blender (bpy).

Picks the primary model file out of an asset group folder and imports it,
dispatching by extension with version-robust operator names (Blender 5.1's
`wm.obj_import` / `wm.stl_import` with legacy fallbacks). Returns the list of
freshly created objects so the rest of the pipeline operates only on this asset.

A failed import raises — the worker turns that into a per-asset failure without
crashing the batch.
"""
from __future__ import annotations

from pathlib import Path

import bpy

from config import SUPPORTED_EXTS  # type: ignore

# Self-contained / texture-bearing formats first; geometry-only last.
_PRIMARY_PRIORITY = {".glb": 0, ".gltf": 1, ".fbx": 2, ".obj": 3, ".stl": 4}


def _enable(addon: str) -> None:
    try:
        bpy.ops.preferences.addon_enable(module=addon)
    except Exception:
        pass


def find_source_files(group_dir) -> list[Path]:
    """All importable model files in the group folder (recursive), priority-sorted."""
    root = Path(group_dir)
    files = [p for p in root.rglob("*")
             if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
    files.sort(key=lambda p: (_PRIMARY_PRIORITY.get(p.suffix.lower(), 99),
                              -p.stat().st_size, p.name.lower()))
    return files


def list_all_files(group_dir) -> list[str]:
    """Every file in the group (models + textures + mtl), repo-relative-ish names."""
    root = Path(group_dir)
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def choose_primary(files: list[Path], group_name: str) -> Path | None:
    """Pick the primary source file: format priority, then a name matching the
    group, then the largest file (already encoded in the sort)."""
    if not files:
        return None
    top_ext = files[0].suffix.lower()
    same = [f for f in files if f.suffix.lower() == top_ext]
    named = [f for f in same if f.stem.lower() == group_name.lower()]
    return (named or same)[0]


def import_file(path) -> list:
    """Import one file by extension; return the newly created objects.

    Raises RuntimeError on an unsupported extension, an importer error, or an
    import that produced no objects.
    """
    p = Path(path)
    ext = p.suffix.lower()
    before = set(bpy.data.objects)

    if ext in (".glb", ".gltf"):
        _enable("io_scene_gltf2")
        bpy.ops.import_scene.gltf(filepath=str(p))
    elif ext == ".obj":
        _import_obj(p)
    elif ext == ".fbx":
        _enable("io_scene_fbx")
        bpy.ops.import_scene.fbx(filepath=str(p))
    elif ext == ".stl":
        _import_stl(p)
    else:
        raise RuntimeError(f"unsupported import extension: {ext}")

    new = [o for o in bpy.data.objects if o not in before]
    if not new:
        raise RuntimeError(f"import produced no objects from {p.name}")
    return new


def _import_obj(p: Path) -> None:
    # Blender 4.0+ ships the C++ importer at wm.obj_import; older builds expose
    # import_scene.obj. Try the modern one first, fall back gracefully.
    if hasattr(bpy.ops.wm, "obj_import"):
        bpy.ops.wm.obj_import(filepath=str(p))
        return
    _enable("io_scene_obj")
    bpy.ops.import_scene.obj(filepath=str(p))


def _import_stl(p: Path) -> None:
    if hasattr(bpy.ops.wm, "stl_import"):
        bpy.ops.wm.stl_import(filepath=str(p))
        return
    _enable("io_mesh_stl")
    bpy.ops.import_mesh.stl(filepath=str(p))
