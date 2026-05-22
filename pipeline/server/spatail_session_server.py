"""
spatail_session_server.py — live-session gateway for the iOS AR player.

Wire contract: docs/xr/REALTIME_PROTOCOL.md
iOS architecture: docs/xr/IOS_APP_ARCHITECTURE.md
Bundle format: docs/xr/IOS_BUNDLE_SPEC.md

Architecture:
    iOS WebSocket  ↔  This server  ↔  Blender worker (MCP / subprocess)
                           │
                           └── HTTP bundle host (USDZ + hero JPGs)

For v1 this is a single-process Python server using:
    - websockets        for the iOS control plane (one async task per client)
    - aiohttp           for the HTTP asset endpoints (signed URLs)
    - subprocess        for orchestrator (Node) and Blender skills (via MCP)

Run:
    python pipeline/server/spatail_session_server.py
        --host 0.0.0.0 --port 8787 --bundle-dir ./bundles

Dependencies (pip install):
    websockets>=12
    aiohttp>=3.9
    jsonpatch>=1.33

Blender integration:
    The server expects a Blender process running with the MCP add-on on the
    standard port. Each pipeline run is a sequence of MCP `execute_blender_code`
    calls driving the existing skill modules. The dispatch table is in
    BLENDER_PIPELINE below.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
    from aiohttp import web
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependencies. Install with:\n"
        "    pip install websockets aiohttp jsonpatch\n"
        f"Original error: {exc}"
    )

try:
    import jsonpatch  # type: ignore
except ImportError:
    jsonpatch = None  # patches will be emitted only when this is available

log = logging.getLogger("spatail.server")


# ────────────────────────────────────────────────────────────────────────
# Closed vocab (mirrors REALTIME_PROTOCOL.md)
# ────────────────────────────────────────────────────────────────────────

INBOUND_EVENT_TYPES = {
    "session.start",
    "session.resync",
    "session.end",
    "user.prompt",
    "room.update",
    "pose.update",
    "interaction.tap",
}

OUTBOUND_EVENT_TYPES = {
    "session.ready",
    "understanding.partial",
    "asset.url",
    "experience.delta",
    "narration.chunk",
    "error",
}

ERROR_CODES = {
    "bad_message",
    "prompt_blocked",
    "asset_not_found",
    "blender_busy",
    "blender_failed",
    "understanding_failed",
    "room_required",
    "rate_limited",
    "unauthorized",
}


# ────────────────────────────────────────────────────────────────────────
# Session state
# ────────────────────────────────────────────────────────────────────────

@dataclass
class Session:
    session_id: str
    ws: WebSocketServerProtocol
    send_seq: int = 0
    last_recv_seq: int = 0
    capabilities: dict[str, Any] = field(default_factory=dict)
    supported_schema_versions: list[str] = field(default_factory=list)
    room_contract: dict[str, Any] | None = None
    user_pose: dict[str, Any] | None = None
    current_bundle_id: str | None = None
    experience_version: int = 0
    last_experience: dict[str, Any] | None = None
    blender_busy: bool = False
    last_prompt_at: float = 0.0

    def next_seq(self) -> int:
        self.send_seq += 1
        return self.send_seq


# ────────────────────────────────────────────────────────────────────────
# Bundle store
# ────────────────────────────────────────────────────────────────────────

class BundleStore:
    """Holds .spatail directories and serves signed URLs.

    For dev: bundles live on local disk under ``bundle_dir``. Production
    swaps this for S3 + CloudFront.
    """

    def __init__(self, bundle_dir: Path, signing_secret: bytes, ttl_seconds: int = 300):
        self.bundle_dir = bundle_dir
        self.bundle_dir.mkdir(parents=True, exist_ok=True)
        self.signing_secret = signing_secret
        self.ttl_seconds = ttl_seconds

    def register_bundle(self, bundle_id: str, stage_dir: Path) -> None:
        """Move a freshly-baked bundle stage into the public dir."""
        dest = self.bundle_dir / bundle_id
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        stage_dir.rename(dest)

    def sign_url(self, base_url: str, bundle_id: str, file: str) -> tuple[str, int]:
        expiry = int(time.time()) + self.ttl_seconds
        payload = f"{bundle_id}/{file}/{expiry}"
        sig = hmac.new(self.signing_secret, payload.encode(),
                        hashlib.sha256).hexdigest()[:32]
        url = f"{base_url}/v1/assets/{bundle_id}/{file}?exp={expiry}&sig={sig}"
        return url, expiry

    def verify_sig(self, bundle_id: str, file: str, exp: int, sig: str) -> bool:
        if time.time() > exp:
            return False
        payload = f"{bundle_id}/{file}/{exp}"
        expected = hmac.new(self.signing_secret, payload.encode(),
                             hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(expected, sig)

    def file_path(self, bundle_id: str, file: str) -> Path | None:
        path = self.bundle_dir / bundle_id / file
        if not path.exists() or not path.is_file():
            return None
        # Prevent path traversal
        try:
            path.resolve().relative_to((self.bundle_dir / bundle_id).resolve())
        except ValueError:
            return None
        return path


# ────────────────────────────────────────────────────────────────────────
# Blender worker — drives the existing skill modules via the MCP bridge.
# ────────────────────────────────────────────────────────────────────────

class BlenderWorker:
    """Bridge to a long-lived Blender process with the MCP add-on running.

    For v1, the bridge is a stub: it logs the pipeline steps and writes a
    placeholder bundle by invoking the existing exporter against whatever
    Blender already has loaded. Wire the actual MCP client (HTTP / IPC) here
    once you decide on the transport.
    """

    def __init__(self, mcp_url: str | None = None):
        self.mcp_url = mcp_url  # e.g. http://127.0.0.1:9876 — left as None for now
        self.lock = asyncio.Lock()
        self.bundles_dir = Path(os.environ.get("SPATAIL_BUNDLES_DIR", "./bundles"))

    async def run_pipeline(self, *, prompt: str, asset_path: str | None,
                            session: Session,
                            send_partial) -> dict[str, Any]:
        """Drive the SPATAIL skill chain. Returns the resulting bundle info.

        send_partial: coroutine callable(stage, label, progress)
        """
        async with self.lock:
            session.blender_busy = True
            try:
                # Pipeline order (matches skills/spatail-toolset/README.md):
                #   1. scale-normalize
                #   2. treat-mesh
                #   3. merge-intelligence (or cluster-parts)
                #   4. pre-classification-audit
                #   5. classify-*
                #   6. measure-per-part (engines)
                #   7. reassemble / rig
                #   8. animate
                #   9. export-xr
                stages = [
                    ("scale_normalize",   "Normalising scale"),
                    ("treat_mesh",         "Cleaning topology"),
                    ("merge_intelligence", "Merging sub-meshes"),
                    ("audit",              "Auditing prompt vs asset"),
                    ("classify",           "Classifying parts"),
                    ("animate",            "Building animation"),
                    ("export_xr",          "Packaging XR bundle"),
                ]
                for i, (stage, label) in enumerate(stages):
                    await send_partial(stage=stage, label=label,
                                       progress=(i + 1) / len(stages))
                    # Hook here: call self._invoke_blender_skill(stage, …)
                    # For v1 we just sleep briefly so the iOS UI shows progress.
                    await asyncio.sleep(0.25)

                # The actual exporter call would land here. For now, return a
                # dummy bundle pointer that the server's HTTP layer will 404.
                bundle_id = f"bundle_{uuid.uuid4().hex[:12]}"
                return {
                    "bundle_id": bundle_id,
                    "byte_size": 0,
                    "scene_present": False,
                    "experience": _placeholder_experience(prompt),
                }
            finally:
                session.blender_busy = False


def _placeholder_experience(prompt: str) -> dict[str, Any]:
    """Minimal v0.5 contract for stub responses while Blender is wired up."""
    return {
        "schemaVersion": "0.5.0-spatail",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "experienceId": "stub_" + uuid.uuid4().hex[:8],
        "title": prompt[:80],
        "sourcePrompt": prompt,
        "spatialElements": [],
        "relationships": [],
        "interactionPlan": {"interactions": []},
        "attentionPlan": [],
        "assetRequirements": [],
        "animations": [],
        "interactions": [],
        "sequences": [],
        "defaultSequenceId": None,
        "roomContract": None,
        "explanation": {"written": prompt, "intentSummary": ""},
        "mechanics": [],
        "presentation": {"layout": "stage_in_front", "ordering": []},
        "reasoningSummary": "stub response from session server",
    }


# ────────────────────────────────────────────────────────────────────────
# WebSocket handler
# ────────────────────────────────────────────────────────────────────────

class SessionServer:
    def __init__(self, bundle_store: BundleStore, blender: BlenderWorker,
                 base_url: str = "http://localhost:8787"):
        self.bundle_store = bundle_store
        self.blender = blender
        self.base_url = base_url
        self.sessions: dict[str, Session] = {}

    async def handle_ws(self, ws: WebSocketServerProtocol):
        session = Session(session_id=f"sess_{uuid.uuid4().hex[:16]}", ws=ws)
        self.sessions[session.session_id] = session
        log.info(f"[{session.session_id}] connected from {ws.remote_address}")
        try:
            async for raw in ws:
                await self._handle_message(session, raw)
        except websockets.ConnectionClosed:
            pass
        finally:
            self.sessions.pop(session.session_id, None)
            log.info(f"[{session.session_id}] disconnected")

    async def _handle_message(self, session: Session, raw: str | bytes):
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            msg = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            await self._send_error(session, "bad_message", "Invalid JSON")
            return

        mtype = msg.get("type")
        if mtype not in INBOUND_EVENT_TYPES:
            await self._send_error(session, "bad_message",
                                    f"Unknown event type: {mtype}")
            return
        session.last_recv_seq = msg.get("seq", session.last_recv_seq)

        payload = msg.get("payload", {})

        handler = {
            "session.start":    self._handle_session_start,
            "session.resync":   self._handle_session_resync,
            "session.end":      self._handle_session_end,
            "user.prompt":      self._handle_user_prompt,
            "room.update":      self._handle_room_update,
            "pose.update":      self._handle_pose_update,
            "interaction.tap":  self._handle_interaction_tap,
        }[mtype]
        try:
            await handler(session, payload)
        except Exception as exc:  # pragma: no cover
            log.exception(f"[{session.session_id}] handler {mtype} failed")
            await self._send_error(session, "blender_failed", str(exc))

    # -- handlers ---------------------------------------------------------

    async def _handle_session_start(self, session: Session, payload: dict):
        session.capabilities = payload.get("capabilities", {})
        session.supported_schema_versions = payload.get(
            "supportedBundleSchemaVersions", [])
        await self._send(session, "session.ready", {
            "sessionId": session.session_id,
            "serverVersion": "0.1.0",
            "bundleSchemaVersion": "0.5.0-spatail-bundle",
        })

    async def _handle_session_resync(self, session: Session, payload: dict):
        if session.last_experience is None:
            await self._send_error(session, "asset_not_found",
                                    "No active experience to resync to")
            return
        await self._send(session, "experience.delta", {
            "version": session.experience_version,
            "kind": "full",
            "experience": session.last_experience,
        })

    async def _handle_session_end(self, session: Session, payload: dict):
        await session.ws.close()

    async def _handle_user_prompt(self, session: Session, payload: dict):
        # Rate limit: 1 prompt every 2 seconds
        now = time.time()
        if now - session.last_prompt_at < 2.0:
            await self._send_error(session, "rate_limited",
                                    "Prompts are limited to 1 every 2s",
                                    retry_after_ms=int(2000 - (now - session.last_prompt_at) * 1000))
            return
        session.last_prompt_at = now

        prompt_text = (payload.get("text") or "").strip()
        if not prompt_text:
            await self._send_error(session, "bad_message", "Empty prompt")
            return

        if session.blender_busy:
            await self._send_error(session, "blender_busy",
                                    "Worker is rendering another asset",
                                    retry_after_ms=6000)
            return

        async def send_partial(*, stage: str, label: str, progress: float):
            await self._send(session, "understanding.partial",
                              {"stage": stage, "label": label,
                               "progress": round(progress, 3)})

        result = await self.blender.run_pipeline(
            prompt=prompt_text,
            asset_path=None,
            session=session,
            send_partial=send_partial,
        )

        bundle_id = result["bundle_id"]
        session.current_bundle_id = bundle_id
        session.experience_version += 1
        session.last_experience = result["experience"]

        # Asset URL (signed). For the stub these point at non-existent files,
        # which the iOS app should fall back from gracefully.
        scene_url, _ = self.bundle_store.sign_url(self.base_url, bundle_id, "scene.usdz")
        hero_url,  _ = self.bundle_store.sign_url(self.base_url, bundle_id, "hero/thumbnail.jpg")

        await self._send(session, "asset.url", {
            "bundleId": bundle_id,
            "sceneUsdz": scene_url,
            "heroThumbnail": hero_url,
            "byteSize": result.get("byte_size", 0),
            "etag": f"W/\"{bundle_id}\"",
        })

        await self._send(session, "experience.delta", {
            "version": session.experience_version,
            "kind": "full",
            "experience": result["experience"],
        })

    async def _handle_room_update(self, session: Session, payload: dict):
        # Merge delta into stored room contract
        if session.room_contract is None or payload.get("kind") == "full":
            session.room_contract = payload
        else:
            existing_planes = {p["id"]: p
                                for p in (session.room_contract.get("planes") or [])}
            for p in payload.get("added", []):
                existing_planes[p["id"]] = p
            for p in payload.get("changed", []):
                if p["id"] in existing_planes:
                    existing_planes[p["id"]].update(p)
            for pid in payload.get("removed", []):
                existing_planes.pop(pid, None)
            session.room_contract = {
                "planes": list(existing_planes.values()),
                "userPose": payload.get("userPose", session.room_contract.get("userPose")),
            }

        if session.last_experience is None:
            return  # nothing to replan against

        # Replan: call out to room_aware_planner.js. For v1, we emit a
        # synthetic patch that just touches the `roomContract` field — the
        # actual replanning hook lands here.
        if jsonpatch is None:
            return
        old = session.last_experience
        new = dict(old)
        new["roomContract"] = session.room_contract
        patch = jsonpatch.JsonPatch.from_diff(old, new).patch
        if not patch:
            return
        session.last_experience = new
        session.experience_version += 1
        await self._send(session, "experience.delta", {
            "version": session.experience_version,
            "kind": "patch",
            "patches": patch,
        })

    async def _handle_pose_update(self, session: Session, payload: dict):
        session.user_pose = payload
        # No outbound by default. Hooks (attention_camera_hint, user_relative
        # anchors) would emit experience.delta patches here.

    async def _handle_interaction_tap(self, session: Session, payload: dict):
        element_id = payload.get("elementId")
        if not session.last_experience or not element_id:
            return
        # Look for interactions[] matching this tap target and execute
        # advance_step / play_animation actions. v1: just emit a partial that
        # iOS can ignore.
        await self._send(session, "understanding.partial", {
            "stage": "interaction",
            "label": f"tap on {element_id}",
            "progress": 1.0,
        })

    # -- send helpers -----------------------------------------------------

    async def _send(self, session: Session, mtype: str, payload: dict):
        if mtype not in OUTBOUND_EVENT_TYPES:
            log.warning(f"Refusing to send unknown event type: {mtype}")
            return
        msg = {
            "type": mtype,
            "seq": session.next_seq(),
            "sentAt": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        try:
            await session.ws.send(json.dumps(msg))
        except websockets.ConnectionClosed:
            pass

    async def _send_error(self, session: Session, code: str, message: str,
                           retry_after_ms: int | None = None):
        if code not in ERROR_CODES:
            code = "bad_message"
        payload = {"code": code, "message": message}
        if retry_after_ms is not None:
            payload["retryAfterMs"] = retry_after_ms
        await self._send(session, "error", payload)


# ────────────────────────────────────────────────────────────────────────
# HTTP asset endpoints (USDZ + hero JPGs)
# ────────────────────────────────────────────────────────────────────────

def build_http_app(bundle_store: BundleStore) -> web.Application:
    app = web.Application()

    async def serve_asset(request: web.Request):
        bundle_id = request.match_info["bundleId"]
        file = request.match_info["file"]
        try:
            exp = int(request.query["exp"])
            sig = request.query["sig"]
        except (KeyError, ValueError):
            raise web.HTTPBadRequest(reason="missing exp/sig")
        if not bundle_store.verify_sig(bundle_id, file, exp, sig):
            raise web.HTTPForbidden(reason="invalid signature")
        path = bundle_store.file_path(bundle_id, file)
        if path is None:
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def health(_request: web.Request):
        return web.json_response({"ok": True, "ts": datetime.now(timezone.utc).isoformat()})

    app.router.add_get("/v1/assets/{bundleId}/{file:.+}", serve_asset)
    app.router.add_get("/v1/health", health)
    return app


# ────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────

async def amain(args):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    signing_secret = os.environ.get(
        "SPATAIL_SIGNING_SECRET", secrets.token_hex(32)
    ).encode()

    bundle_store = BundleStore(
        bundle_dir=Path(args.bundle_dir),
        signing_secret=signing_secret,
    )
    blender = BlenderWorker(mcp_url=args.mcp_url)
    session_server = SessionServer(
        bundle_store=bundle_store,
        blender=blender,
        base_url=f"http://{args.host}:{args.port}",
    )

    # HTTP server (assets + health)
    http_app = build_http_app(bundle_store)
    http_runner = web.AppRunner(http_app)
    await http_runner.setup()
    http_site = web.TCPSite(http_runner, args.host, args.port)
    await http_site.start()
    log.info(f"HTTP listening on http://{args.host}:{args.port}")

    # WebSocket server (separate port for clarity)
    ws_port = args.port + 1
    ws_server = await websockets.serve(
        session_server.handle_ws,
        host=args.host,
        port=ws_port,
        max_size=64 * 1024,
        ping_interval=20,
        ping_timeout=20,
    )
    log.info(f"WebSocket listening on ws://{args.host}:{ws_port}/v1/session")
    log.info("Ready. Connect from iOS with the SPATAIL player.")
    log.info("Stop with Ctrl-C.")

    try:
        await asyncio.Future()  # run forever
    finally:
        ws_server.close()
        await ws_server.wait_closed()
        await http_runner.cleanup()


def main():
    parser = argparse.ArgumentParser(description="SPATAIL session server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787,
                        help="HTTP port; WebSocket is HTTP+1")
    parser.add_argument("--bundle-dir", default="./bundles")
    parser.add_argument("--mcp-url", default=None,
                        help="Blender MCP bridge URL (TBD)")
    args = parser.parse_args()
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
