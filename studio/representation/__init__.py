"""SPATAIL Representation Engine — the "brain" of the studio pipeline.

Turns a free-text prompt into an Experience Plan (how to present the concept
spatially) and an Asset Request Manifest (what the Blender Asset Factory must
produce). Deterministic + offline by default; an optional `claude`-CLI refine
hook can sharpen the classification when use_llm=True.

This package follows the studio repo's flat-import convention: there are no
installed packages, so importing this package first puts `studio/` and
`studio/server/` on sys.path. That lets every module here `import xr_design`
and `import experience_spec` exactly like contract.py / job_server.py do, while
the package's own modules stay cleanly namespaced (representation.types, …) and
never collide with the stdlib `types` module.
"""
import os as _os
import sys as _sys

_STUDIO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))  # studio/
for _p in (_STUDIO, _os.path.join(_STUDIO, "server")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
