"""Tiny HTTP server that wraps the orchestrator and serves /api/ask.

Stdlib only (http.server) — no Flask dependency. Loads ANTHROPIC_API_KEY
from intelligence/.env so the key never lives in source.

Run:
    python -m intelligence.server          # from engineexplainer/
    # or
    python intelligence/server.py
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Make the package importable when run as `python intelligence/server.py`
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

# Load .env BEFORE importing orchestrator. override=True so the .env value
# beats any stale ANTHROPIC_API_KEY in the shell env (otherwise dotenv would
# silently keep the shell's value and the SDK would reject our key).
try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env", override=True)
    print(f"[server] .env loaded; key fingerprint: ...{os.environ.get('ANTHROPIC_API_KEY', '')[-8:]}")
except ImportError:
    print("[server] python-dotenv not installed; relying on shell env")

from intelligence import orchestrator
from intelligence.tools.contract_actions import DirectorError

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

PORT = int(os.environ.get("ENGINEEXPLAINER_PORT", "5175"))
ALLOWED_ORIGINS = "*"  # the web runtime is local; widen later only if needed

# -----------------------------------------------------------------------------
# Request handler
# -----------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):

    # ---- CORS preflight ----
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True, "model": os.environ.get("ENGINEEXPLAINER_MODEL", "claude-sonnet-4-5")})
            return
        if self.path == "/api/last":
            # Return the most recent saved contract (no LLM call). Useful for
            # re-testing runtime changes against an existing contract.
            try:
                from pathlib import Path
                p = Path(r"C:\tmp\last_live_contract.json")
                if not p.exists():
                    self._json(404, {"error": "no saved contract"})
                    return
                self._json(200, json.loads(p.read_text()))
            except Exception as e:
                self._json(500, {"error": "read_failed", "detail": str(e)})
            return
        self._json(404, {"error": f"no GET handler for {self.path}"})

    def do_POST(self):
        if self.path == "/api/ask":
            return self._handle_ask()
        if self.path == "/api/capture":
            return self._handle_capture()
        if self.path == "/api/validate-asset":
            return self._handle_validate_asset()
        if self.path == "/api/manual":
            return self._handle_manual()
        self._json(404, {"error": f"unknown endpoint {self.path}"})

    def _handle_ask(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            data = json.loads(body or "{}")
        except Exception as e:
            self._json(400, {"error": "bad json", "detail": str(e)})
            return

        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            self._json(400, {"error": "prompt is required"})
            return

        history = data.get("history") or []
        asset_id = (data.get("asset_id") or data.get("asset") or "engine").lower()

        print(f"[server] POST /api/ask  asset={asset_id!r}  prompt={prompt!r}")
        try:
            contract = orchestrator.answer(prompt, history=history, asset_id=asset_id)
            # Save the most recent contract to disk so the visual-validator
            # CLI can pick it up without re-running the orchestrator.
            try:
                from pathlib import Path
                Path(r"C:\tmp\last_live_contract.json").write_text(json.dumps(contract, indent=2))
            except Exception: pass
            self._json(200, contract)
        except DirectorError as e:
            traceback.print_exc()
            self._json(422, {"error": "director_failed", "detail": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"error": "intelligence_failed", "detail": str(e),
                             "type": type(e).__name__})

    def _handle_manual(self):
        """usermanualXR: POST {manual_text, mode?} -> build a walkthrough.

        mode="generate" (default): SEGMENT the manual into parts+steps, BUILD the
            asset part-by-part in headless Blender, register it, and stage an
            assembly walkthrough. Never collapses onto a curated model. This is
            the manual->XR mini-app path.
        mode="match": classify the product and match a CURATED library model,
            then stage + bake its steps (the legacy explainer-drawer path)."""
        try:
            from . import walkthrough
        except ImportError:
            import walkthrough
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            manual_text = (data.get("manual_text") or "").strip()
            if not manual_text:
                self._json(400, {"error": "manual_text is required"})
                return
            mode = (data.get("mode") or "generate").strip().lower()
            run_bake = bool(data.get("run_bake", True))
            print(f"[server] POST /api/manual  mode={mode!r}  ({len(manual_text)} chars)")
            result = walkthrough.build_walkthrough(manual_text, mode=mode, run_bake=run_bake)
            if result.get("ok"):
                try:
                    from pathlib import Path
                    Path(r"C:\tmp\last_walkthrough.json").write_text(
                        json.dumps(result["contract"], indent=2))
                except Exception:
                    pass
                self._json(200, result)
            else:
                self._json(422, result)
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"error": "manual_failed", "detail": str(e),
                             "type": type(e).__name__})

    def _handle_validate_asset(self):
        """Run the motion validator against an asset, return its PASS/WARN/FAIL.

        POST body: {asset_id, asset_glb?, registry_path?, anim_library_path?,
                    blend_path?, out_root?}
        Defaults: paths are derived from asset_id under engineexplainer/engine/.
        Spawns Blender headlessly (~6-12s per clip)."""
        try:
            from . import motion_validator_agent as mva
        except ImportError:
            import motion_validator_agent as mva
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            asset_id = data.get("asset_id") or "fan"
            base = HERE.parent / "engine"
            asset_glb         = data.get("asset_glb")         or str(base / f"{asset_id if asset_id != 'engine' else 'v8_engine'}.glb")
            registry_path     = data.get("registry_path")     or str(base / (f"{asset_id}_part_registry.json" if asset_id != "engine" else "part_registry.json"))
            anim_library_path = data.get("anim_library_path") or str(base / (f"{asset_id}_animation_library.json" if asset_id != "engine" else "animation_library.json"))
            blend_path        = data.get("blend_path")        # let agent guess if None
            out_root          = data.get("out_root")          or r"C:/tmp/motion_validate"
            print(f"[server] POST /api/validate-asset  asset_id={asset_id!r}")
            report = mva.validate(
                asset_id=asset_id, asset_glb=asset_glb,
                registry_path=registry_path, anim_library_path=anim_library_path,
                blend_path=blend_path, out_root=out_root,
            )
            self._json(200, {
                "asset_id": report.asset_id,
                "overall_verdict": report.overall_verdict,
                "out_root": report.out_root,
                "clips": [{
                    "clip": c.clip, "verdict": c.verdict,
                    "motion_consistency_score": c.motion_consistency_score,
                    "stationary_but_should_have_moved": c.stationary_but_should_have_moved,
                    "moved_but_not_in_group": c.moved_but_not_in_group,
                    "contact_sheet": c.contact_sheet,
                } for c in report.clips],
            })
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"error": "validate_failed", "detail": str(e), "type": type(e).__name__})

    def _handle_capture(self):
        """Receive {beat_id, contract_id, image_b64} from the browser.
        Decodes the PNG to disk under C:\\tmp\\engineexplainer_captures\\<contract_id>\\<beat_id>.png
        so the visual validator can read them back."""
        try:
            import base64, os
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            data = json.loads(body or "{}")
            beat_id = data.get("beat_id") or "unnamed"
            contract_id = data.get("contract_id") or "unknown_contract"
            image = data.get("image_b64") or ""
            if image.startswith("data:image/"):
                image = image.split(",", 1)[1]
            img_bytes = base64.b64decode(image)
            out_dir = os.path.join(r"C:\tmp\engineexplainer_captures", contract_id)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{beat_id}.png")
            with open(out_path, "wb") as f:
                f.write(img_bytes)
            # ASCII print so Windows cp1252 console doesn't crash on the arrow
            print(f"[server] captured beat '{beat_id}' -> {out_path} ({len(img_bytes)} bytes)")
            self._json(200, {"ok": True, "path": out_path, "size": len(img_bytes)})
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"error": "capture_failed", "detail": str(e)})

    # ---- helpers ----
    def _json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGINS)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt, *args):
        # Quieter default log line
        sys.stderr.write("[server] %s\n" % (fmt % args))


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[server] WARNING: ANTHROPIC_API_KEY not in env. /api/ask will fail.")
        print(f"[server] Looked for .env at: {HERE / '.env'}")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[server] engineexplainer intelligence listening on http://127.0.0.1:{PORT}")
    print(f"[server] model: {os.environ.get('ENGINEEXPLAINER_MODEL', 'claude-sonnet-4-5')}")
    print(f"[server] try: curl -X POST http://127.0.0.1:{PORT}/api/ask -H 'Content-Type: application/json' -d '{{\"prompt\":\"How does a piston work?\"}}'")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
