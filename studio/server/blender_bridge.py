"""blender_bridge.py - minimal socket client for the LIVE Blender MCP bridge.

The first-party Blender "MCP" add-on (blender.org/lab/mcp-server/, auto-start)
listens on localhost:9876 and speaks a tiny wire protocol:

    request  : json.dumps({"type":"execute","code":<py>,"strict_json":<bool>}) + b"\\0"
    response : json.dumps({"status":"ok","result":{...}, ...}) + b"\\0"
               (or {"status":"error","message": <traceback>})

The executed code runs in Blender's main thread with full `bpy` access, in a
fresh namespace where `result = {}` is pre-defined and NOTHING is imported - every
snippet must `import bpy` itself. To return data, assign a JSON-serialisable dict
to `result`.

Dependency-free (stdlib only) so it runs under any Python.
"""
from __future__ import annotations

import json
import socket

HOST = "127.0.0.1"
PORT = 9876


class BridgeError(RuntimeError):
    """Raised when Blender is unreachable or the executed code reports an error."""


def run_code(code: str, *, timeout: float = 180.0, strict_json: bool = True) -> dict:
    """Execute `code` inside the live Blender and return its `result` dict.

    Raises BridgeError if Blender can't be reached or the snippet errors.
    """
    request = json.dumps(
        {"type": "execute", "code": code, "strict_json": strict_json}
    ).encode("utf-8") + b"\0"

    try:
        with socket.create_connection((HOST, PORT), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(request)
            buf = bytearray()
            while b"\0" not in buf:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf.extend(chunk)
    except (ConnectionRefusedError, socket.timeout, OSError) as exc:
        raise BridgeError(
            f"cannot reach the Blender MCP bridge on {HOST}:{PORT} "
            f"(is Blender open with the MCP server started?): {exc}"
        ) from exc

    if b"\0" not in buf:
        raise BridgeError("Blender bridge closed the connection without a full response")

    try:
        resp = json.loads(bytes(buf[: buf.index(b"\0")]))
    except json.JSONDecodeError as exc:
        raise BridgeError(f"malformed response from Blender bridge: {exc}") from exc

    if resp.get("status") != "ok":
        msg = (resp.get("message") or resp.get("stderr") or "unknown Blender error").strip()
        raise BridgeError(msg)

    result = resp.get("result", {})
    return result if isinstance(result, dict) else {"value": result}


def ping(timeout: float = 5.0) -> dict | None:
    """Return {'pong': True, 'blender': <version>} if the live bridge answers, else None."""
    try:
        return run_code(
            "import bpy\nresult = {'pong': True, 'blender': bpy.app.version_string}",
            timeout=timeout,
        )
    except BridgeError:
        return None
