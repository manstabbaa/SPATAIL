"""spatail_live_blender.py — push a built asset into the LIVE Blender session.

The user keeps one Blender open with the lab_blender_org MCP add-on running; it
listens on a TCP socket (localhost:9876) for null-byte-delimited JSON requests:

    {"type": "execute", "code": "<python>", "strict_json": true}\\0

The add-on `exec()`s the code with a predefined ``result = {}`` dict in scope and
replies with ``{"status":"ok","result":{...},"stdout":...}\\0`` (or
``{"status":"error","message":...}``). See the add-on's
``mcp_to_blender_server.py`` for the wire format.

This module is the pipeline-side CLIENT of that protocol — pure stdlib, no
dependencies, so the headless generative bridge can import it freely. It mirrors
a freshly-built GLB into the user's live scene at x1.0 (the GLB is already
metres, Y-up; Blender's glTF importer converts to Z-up), isolated in a dedicated
``SPATAIL_<assetId>`` collection so the user's own objects are never touched.

Everything here is best-effort: if Blender is closed or the add-on is offline,
callers get ``{"ok": False, "skipped": True, "reason": ...}`` and must carry on —
the headless build + web mini-app do not depend on the live session.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

DEFAULT_HOST = os.environ.get("ENGINEEXPLAINER_BLENDER_MCP_HOST", "localhost")
DEFAULT_PORT = int(os.environ.get("ENGINEEXPLAINER_BLENDER_MCP_PORT", "9876"))

_NULL = b"\0"
_RECV = 4096


# ── raw protocol ──────────────────────────────────────────────────────────

def send_code(code: str, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
              timeout: float = 30.0, strict_json: bool = True) -> dict:
    """Send Python *code* to the live Blender add-on and return its JSON reply.

    The add-on requires a ``strict_json`` boolean and a ``result`` dict assigned
    by the code. Raises on socket/JSON failure (callers wrap as needed).
    """
    req = json.dumps({"type": "execute", "code": code,
                      "strict_json": strict_json}).encode("utf-8") + _NULL
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(req)
        buf = bytearray()
        while _NULL not in buf:
            chunk = s.recv(_RECV)
            if not chunk:
                break
            buf.extend(chunk)
    end = buf.index(_NULL) if _NULL in buf else len(buf)
    return json.loads(bytes(buf[:end]).decode("utf-8"))


def is_live(*, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
            timeout: float = 1.5) -> bool:
    """Quick reachability probe: true iff the add-on answers an exec ping."""
    try:
        resp = send_code("result = {'pong': True}", host=host, port=port,
                         timeout=timeout)
        return resp.get("status") == "ok"
    except Exception:
        return False


# ── import a GLB into a dedicated collection in the live scene ──────────────

# Body runs inside Blender with GLB_PATH + ASSET_ID predefined; assigns `result`.
_IMPORT_BODY = r'''
import bpy
from mathutils import Vector

COLL = "SPATAIL_" + ASSET_ID

# Refresh our managed collection only (never touch user objects/collections).
old = bpy.data.collections.get(COLL)
if old is not None:
    for ob in list(old.objects):
        me = ob.data if ob.type == 'MESH' else None
        bpy.data.objects.remove(ob, do_unlink=True)
        if me is not None and getattr(me, "users", 1) == 0:
            try:
                bpy.data.meshes.remove(me)
            except Exception:
                pass
    for parent in list(bpy.data.collections) + [bpy.context.scene.collection]:
        try:
            if COLL in [c.name for c in parent.children]:
                parent.children.unlink(old)
        except Exception:
            pass
    try:
        bpy.data.collections.remove(old)
    except Exception:
        pass

# Hide the factory default cube if present (non-destructive, reversible).
cube = bpy.data.objects.get("Cube")
if cube is not None and cube.type == 'MESH':
    cube.hide_viewport = True
    cube.hide_render = True

coll = bpy.data.collections.new(COLL)
bpy.context.scene.collection.children.link(coll)
lc = bpy.context.view_layer.layer_collection.children.get(COLL)
if lc is not None:
    bpy.context.view_layer.active_layer_collection = lc

before = set(bpy.data.objects.keys())
bpy.ops.import_scene.gltf(filepath=GLB_PATH)
new = [bpy.data.objects[n] for n in bpy.data.objects.keys() if n not in before]

# Make sure imports live only in our collection.
for ob in new:
    for c in list(ob.users_collection):
        if c is not coll:
            try:
                c.objects.unlink(ob)
            except Exception:
                pass
    if ob.name not in coll.objects:
        try:
            coll.objects.link(ob)
        except Exception:
            pass

meshes = [o for o in new if o.type == 'MESH']
deps = bpy.context.evaluated_depsgraph_get()
mn = Vector((1e18, 1e18, 1e18)); mx = Vector((-1e18, -1e18, -1e18))
tris = 0
for o in meshes:
    ev = o.evaluated_get(deps)
    me = ev.to_mesh()
    me.calc_loop_triangles()
    tris += len(me.loop_triangles)
    for v in me.vertices:
        w = o.matrix_world @ v.co
        for i in range(3):
            if w[i] < mn[i]: mn[i] = w[i]
            if w[i] > mx[i]: mx[i] = w[i]
    ev.to_mesh_clear()
ext = [round(mx[i] - mn[i], 4) for i in range(3)] if meshes else [0, 0, 0]

# Distinct per-object colour + frame the viewport (best-effort, never fatal).
try:
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in meshes:
        o.select_set(True)
    if meshes:
        bpy.context.view_layer.objects.active = meshes[0]
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            sp = area.spaces.active
            sp.shading.type = 'SOLID'
            sp.shading.color_type = 'OBJECT'
            for region in area.regions:
                if region.type == 'WINDOW':
                    with bpy.context.temp_override(area=area, region=region):
                        bpy.ops.view3d.view_selected()
                    break
            break
except Exception:
    pass

result = {
    "collection": COLL,
    "parts": [o.name for o in meshes],
    "n_meshes": len(meshes),
    "tris": tris,
    "extents_m": ext,
    "z_min": round(mn[2], 4) if meshes else 0.0,
    "z_max": round(mx[2], 4) if meshes else 0.0,
}
'''


def import_glb_into_live(glb_path: str, asset_id: str, *,
                         host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                         timeout: float = 60.0) -> dict:
    """Import *glb_path* into a ``SPATAIL_<asset_id>`` collection in the live
    session and frame it. Returns the raw add-on reply dict."""
    glb = str(glb_path).replace("\\", "/")
    header = "GLB_PATH = {!r}\nASSET_ID = {!r}\n".format(glb, asset_id)
    return send_code(header + _IMPORT_BODY, host=host, port=port, timeout=timeout)


def mirror_asset_to_live(glb_path: str, asset_id: str, *,
                         host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                         timeout: float = 60.0) -> dict:
    """Best-effort: push a built GLB into the live Blender session.

    Returns ``{"ok": bool, "skipped": bool, "reason"?: str, **result}``. Never
    raises — a closed Blender simply yields ``skipped=True``.
    """
    p = Path(glb_path)
    if not p.exists():
        return {"ok": False, "skipped": True, "reason": f"glb not found: {glb_path}"}
    if not is_live(host=host, port=port):
        return {"ok": False, "skipped": True,
                "reason": f"no live Blender on {host}:{port}"}
    try:
        resp = import_glb_into_live(str(p), asset_id, host=host, port=port,
                                    timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "skipped": True, "reason": f"socket error: {e}"}
    if resp.get("status") != "ok":
        return {"ok": False, "skipped": False,
                "reason": resp.get("message", "exec error"), "raw": resp}
    out = {"ok": True, "skipped": False}
    out.update(resp.get("result", {}))
    return out


if __name__ == "__main__":
    import sys
    glb = sys.argv[1] if len(sys.argv) > 1 else \
        str(Path(__file__).resolve().parents[2] / "engineexplainer" / "engine" / "gen_kallax.glb")
    aid = sys.argv[2] if len(sys.argv) > 2 else "gen_kallax"
    print(f"live? {is_live()}")
    print(json.dumps(mirror_asset_to_live(glb, aid), indent=2))
