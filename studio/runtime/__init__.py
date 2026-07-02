"""SPATAIL Runtime layer — assemble, place, sequence, and progressively load.

Takes the Experience Plan (brain) + Asset Delivery Package (factory) and produces
the Runtime Scene Contract (layout happens HERE, via studio/xr_design — never in
Blender), a Progressive Load Plan (anchor + placeholder + title within ~1 s with
no Blender, then stream the real assets), and the Interaction Orchestration that
gates interactions/animations until their assets are ready.

Importing this package puts studio/ and studio/server/ on sys.path so it can
`import xr_design` / `import experience_spec` and reach the rest of the subsystem.
"""
import os as _os
import sys as _sys

_STUDIO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))  # studio/
for _p in (_STUDIO, _os.path.join(_STUDIO, "server")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
