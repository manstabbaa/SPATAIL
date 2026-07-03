"""job_server.py - SPATAIL generative AR job server (PC side).

Implements docs/generative_ar_contract.md exactly:

    POST /jobs            {prompt, client}      -> {id, status}
    GET  /jobs/{id}                              -> {id, status, stage, message,
                                                     usdz_url?, glb_url?,
                                                     manifest_url?, metadata_url?}
    GET  /artifacts/{file}                       -> USDZ / GLB / metadata bytes
    GET  /health                                 -> liveness + Blender bridge status

The model field returned by GET /jobs/{id} is shaped by the client platform
(see _client_platform): Android XR clients ({"client":{"platform":"android"}})
get glb_url + manifest_url as the primary (and only) model fields; iOS/default
clients get usdz_url primary with glb_url/manifest_url additive. This is the
Master File 2026 pivot — Android consumes glTF/GLB, Apple consumes USDZ.

A single background worker serialises jobs (Blender is single-threaded): it drives
the LIVE Blender MCP bridge (localhost:9876) via generator.generate() to model +
animate the prompt and export a Y-up / metres / baked-looping USDZ into
studio/out/gen/, then flips the job to `done`.

Stdlib only - no pip installs. Run:

    python studio/server/job_server.py            # binds 0.0.0.0:8787

Binding 0.0.0.0 means the server answers on whatever interfaces exist, including
the Tailscale adapter once Tailscale is up - no firewall edits, no LAN assumptions
(Windows Firewall still blocks LAN inbound by default; Tailscale permits its own).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from urllib.parse import unquote, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_bridge  # noqa: E402
import generator  # noqa: E402
import director  # noqa: E402
import headless_build  # noqa: E402  (per-phone isolated Blender executor)

# Placement traces + schema truth (LIVE_BRAIN_SPEC §2): every /modular decision is
# persisted for attribution and validated warn-only. Imported defensively — a trace
# problem must NEVER break /modular or take down the spine.
try:
    import trace_store  # noqa: E402
except Exception as _tr_exc:  # noqa: BLE001
    trace_store = None
    print(f"[spine] trace store unavailable: {_tr_exc}", flush=True)

# Representation Engine — opt-in mode="representation". Purely additive: the
# existing "experience"/"object" modes are untouched. Imported defensively so a
# fault in the optional subsystem can never take down the always-on spine.
try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from representation import job_entry  # noqa: E402
except Exception as _rep_exc:  # noqa: BLE001
    job_entry = None
    print(f"[spine] representation mode unavailable: {_rep_exc}", flush=True)

# The asset artist (Meshy) — the DEFAULT path for object generation since the
# tracked/placed pivot: Gemini multi-view → Meshy image-to-3D → normalize +
# decimate → USDZ. Imported defensively; the procedural Blender author remains
# the fallback whenever this is unavailable or a build fails.
try:
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "meshy"))
    import asset_service as meshy_assets  # noqa: E402
except Exception as _meshy_exc:  # noqa: BLE001
    meshy_assets = None
    print(f"[spine] meshy asset path unavailable: {_meshy_exc}", flush=True)

ROOT = Path(__file__).resolve().parents[2]            # C:\SPATAIL_MAX
ARTIFACTS = ROOT / "studio" / "out" / "gen"
ARTIFACTS.mkdir(parents=True, exist_ok=True)

CONTRACT_VERSION = "0.1"

# Where Blender lives (override with BLENDER_EXE). Used by the keep-alive watchdog
# so the always-on spine self-heals if Blender is closed/crashes.
BLENDER_EXE = os.environ.get(
    "BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")


def _keep_awake() -> None:
    """Stop Windows from sleeping / blanking the display while the server runs, so
    the phone can always reach the live Blender. Uses ctypes (stdlib) —
    SetThreadExecutionState with CONTINUOUS keeps the request active for the life
    of this process; cleared automatically when the process exits."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002  # also keep the display on (passthrough demos)
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
        print("[spine] keep-awake engaged (system + display)", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[spine] keep-awake unavailable: {exc}", flush=True)


def _blender_watchdog(poll_seconds: float = 15.0) -> None:
    """Keep the live Blender bridge up. If the socket is unreachable for two
    consecutive checks (Blender closed/crashed), relaunch Blender headed so its
    MCP add-on auto-starts again. Never raises — the spine must not die."""
    if sys.platform != "win32":
        return
    import subprocess
    misses = 0
    launched_at = 0.0
    while True:
        time.sleep(poll_seconds)
        try:
            up = blender_bridge.ping(timeout=4.0) is not None
        except Exception:  # noqa: BLE001
            up = False
        if up:
            misses = 0
            continue
        misses += 1
        # need 2 misses (~30s) before acting, and don't relaunch within 90s of a launch
        if misses < 2 or (time.time() - launched_at) < 90:
            continue
        if not os.path.exists(BLENDER_EXE):
            print(f"[spine] Blender bridge DOWN and exe not found at {BLENDER_EXE}",
                  flush=True)
            misses = 0
            continue
        try:
            print("[spine] Blender bridge DOWN — relaunching Blender…", flush=True)
            subprocess.Popen([BLENDER_EXE], close_fds=True)
            launched_at = time.time()
            misses = 0
        except Exception as exc:  # noqa: BLE001
            print(f"[spine] relaunch failed: {exc}", flush=True)

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
_QUEUE: "Queue[str]" = Queue()                 # intake; the dispatcher drains this

# ── per-session concurrency ("a Blender session per running app") ───────────────
# Each phone sends a stable `session` id. Object builds for DIFFERENT phones run in
# their OWN headless Blender concurrently (isolated); one phone is capped so it
# can't hog the box. Shared-bridge modes (experience/representation/educational)
# all use the single LIVE Blender, so they share one lane and serialise.
GLOBAL_CAP = max(1, int(os.environ.get("SPATAIL_BLENDER_CONCURRENCY", "3")))
SESSION_CAP = max(1, int(os.environ.get("SPATAIL_SESSION_CONCURRENCY", "1")))
ISOLATED = os.environ.get("SPATAIL_ISOLATED_BUILDS", "1").lower() not in (
    "0", "false", "no", "off")
SHARED_SESSION = "__shared_blender__"          # lane for everything on the live spine

_PENDING: "deque[str]" = deque()               # job_ids awaiting dispatch (FIFO)
_INFLIGHT: dict[str, int] = {}                 # session -> builds currently running
_GLOBAL_INFLIGHT = 0
_SCHED = threading.Condition()                 # guards _PENDING/_INFLIGHT/_GLOBAL_INFLIGHT


def _update(job_id: str, **kw) -> None:
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(kw)


def _snapshot(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def _client_platform(job: dict) -> str:
    """Best-effort client platform, used only to shape the wire contract (which
    model field is primary). The POST /jobs body may carry `client` as a dict
    ({"platform":"android", ...}) or a bare string ("android"); anything else
    yields "" → the default iOS/USDZ-first behaviour. Added for the Android XR
    pivot (Master File 2026): Android consumes glTF/GLB, never USDZ."""
    c = job.get("client")
    if isinstance(c, dict):
        return str(c.get("platform") or "").strip().lower()
    if isinstance(c, str):
        return c.strip().lower()
    return ""


def _uses_isolated(job: dict) -> bool:
    """An object/back-compat build runs in its OWN headless Blender when isolated
    mode is on and the executor is actually available; everything else (and the
    fallback) uses the shared live spine."""
    return (ISOLATED and headless_build.available()
            and job.get("mode") in (None, "object"))


def _session_for(job_id: str) -> str:
    """The concurrency lane for a job: the phone's session for isolated per-phone
    object builds; the single shared lane for everything on the live Blender."""
    job = _snapshot(job_id)
    if not job:
        return SHARED_SESSION
    if _uses_isolated(job):
        # per-phone affinity; anon builds get their own lane so they don't serialise
        return f"sess:{job.get('session')}" if job.get("session") else f"job:{job_id}"
    return SHARED_SESSION


def _run_job(job_id: str) -> None:
    """Run ONE job to done/error on a per-build worker thread (spawned by the
    scheduler). The concurrency lane it holds is freed by _run_and_release.

    Object / back-compat builds run in their OWN headless Blender when isolated
    mode is on (true per-phone concurrency); every other mode drives the shared
    live spine and therefore shares a single serialised lane."""
    try:
        job = _snapshot(job_id)
        if not job:
            return
        _update(job_id, status="running", stage="planning", message=None)
        if job.get("mode") == "representation":
            # Representation Engine: prompt -> Experience Plan + Asset Request
            # Manifest + Runtime Scene Contract + Progressive Load Plan.
            if job_entry is None:
                raise RuntimeError("representation mode unavailable on this server")
            res = job_entry.run_job(
                job["prompt"], job_id, ARTIFACTS,
                on_stage=lambda s, _id=job_id: _update(_id, stage=s),
            )
            _update(
                job_id, status="done", stage="ready", message=None,
                representation=res["experience_name"], runtime=res["runtime_name"],
                delivery=res["delivery_name"], progressive=res["progressive_name"],
                plan_artifact=res["plan_name"], title=res.get("title"),
                stations=res.get("stations"), finished=time.time(),
            )
            print(f"[job] {job_id} done -> representation {res.get('title')!r} "
                  f"({res.get('strategy')}, {res.get('stations')} assets)", flush=True)
        elif job.get("mode") == "experience":
            # Director: prompt -> multi-station Experience Spec + N USDZs
            res = director.generate_experience(
                job["prompt"], job_id, ARTIFACTS,
                on_stage=lambda s, _id=job_id: _update(_id, stage=s),
            )
            _update(
                job_id, status="done", stage="ready", message=None,
                experience=res["experience_name"], stations=res["stations"],
                title=res["title"], finished=time.time(),
            )
            print(f"[job] {job_id} done -> {res['experience_name']} "
                  f"({res['stations']} stations)", flush=True)
        else:
            # single object. DEFAULT path = the asset artist (Meshy): photoreal
            # mesh from multi-view images, normalized + decimated for AR. The
            # procedural Blender author is the FALLBACK (Meshy unavailable, no
            # keys, API failure, …) — same result shape either way.
            res, lane = None, ""
            asset_req = job.get("asset") or {}
            if meshy_assets is not None and meshy_assets.available():
                try:
                    res = meshy_assets.produce(
                        asset_req.get("subject") or job["prompt"],
                        job["prompt"], job_id, ARTIFACTS,
                        asset_id=asset_req.get("assetId"),
                        scale_meters=asset_req.get("scaleMeters"),
                        category=asset_req.get("category") or "general",
                        story_requirements=asset_req.get("assetRequirements"),
                        on_stage=lambda s, _id=job_id: _update(
                            _id, stage=s, stageIndex=meshy_assets.stage_index(s),
                            stageTotal=len(meshy_assets.MESHY_STAGES)))
                    lane = "meshy"
                except Exception as exc:  # noqa: BLE001
                    print(f"[job] {job_id} meshy path failed ({exc}) — "
                          f"procedural fallback", flush=True)
                    res = None
            if res is None and _uses_isolated(job):
                res = headless_build.build(
                    job["prompt"], job_id, ARTIFACTS,
                    on_stage=lambda s, _id=job_id: _update(_id, stage=s))
                lane = "isolated"
            elif res is None:
                res = generator.generate(
                    job["prompt"], job_id, ARTIFACTS,
                    on_stage=lambda s, _id=job_id: _update(_id, stage=s))
                lane = "shared"
            _update(
                job_id, status="done", stage="ready", message=None,
                usdz=res["usdz_name"], metadata=res.get("metadata_name"),
                bbox_yup=res.get("bbox_yup"), max_dim=res.get("max_dim"),
                spec=res.get("spec"), finished=time.time(),
            )
            print(f"[job] {job_id} done -> {res['usdz_name']} "
                  f"({lane}, max_dim {res.get('max_dim')} m)", flush=True)
    except Exception as exc:  # noqa: BLE001
        _update(job_id, status="error", stage=None, message=str(exc),
                finished=time.time())
        print(f"[job] {job_id} ERROR: {exc}", flush=True)


# ── scheduler: intake -> pending -> dispatch under (global, per-session) caps ───
def _intake() -> None:
    """Move queued job ids into the scheduler's pending deque and wake dispatch."""
    while True:
        job_id = _QUEUE.get()
        with _SCHED:
            _PENDING.append(job_id)
            _SCHED.notify_all()


def _collect_dispatchable() -> list[str]:
    """Pending jobs that may start NOW without breaching the global cap or any
    per-session cap, in FIFO order. Must be called holding _SCHED."""
    room = GLOBAL_CAP - _GLOBAL_INFLIGHT
    if room <= 0:
        return []
    picks: list[str] = []
    proj = dict(_INFLIGHT)
    for jid in _PENDING:
        if len(picks) >= room:
            break
        s = _session_for(jid)
        if proj.get(s, 0) < SESSION_CAP:
            picks.append(jid)
            proj[s] = proj.get(s, 0) + 1
    return picks


def _ahead_of(job_id: str) -> int:
    """How many pending jobs sit before this one — for queue-position reporting so
    the phone shows 'queued (N ahead)' instead of a silent countdown."""
    with _SCHED:
        for i, jid in enumerate(_PENDING):
            if jid == job_id:
                return i
    return 0


def _dispatch_loop() -> None:
    """Start dispatchable jobs on their own worker threads as lanes free up."""
    global _GLOBAL_INFLIGHT
    while True:
        with _SCHED:
            picks = _collect_dispatchable()
            while not picks:
                _SCHED.wait()
                picks = _collect_dispatchable()
            for jid in picks:
                try:
                    _PENDING.remove(jid)
                except ValueError:
                    continue
                s = _session_for(jid)
                _INFLIGHT[s] = _INFLIGHT.get(s, 0) + 1
                _GLOBAL_INFLIGHT += 1
                threading.Thread(target=_run_and_release, args=(jid, s),
                                 daemon=True, name=f"build-{jid}").start()


def _run_and_release(job_id: str, sess: str) -> None:
    """Run a job, then free its global + per-session lane and wake the dispatcher."""
    global _GLOBAL_INFLIGHT
    try:
        _run_job(job_id)
    finally:
        with _SCHED:
            n = _INFLIGHT.get(sess, 1) - 1
            if n <= 0:
                _INFLIGHT.pop(sess, None)
            else:
                _INFLIGHT[sess] = n
            _GLOBAL_INFLIGHT -= 1
            _SCHED.notify_all()


class Handler(BaseHTTPRequestHandler):
    server_version = "SpatailGenJobServer/0.1"

    # -- helpers --------------------------------------------------------------
    def _send(self, code: int, *, obj=None, raw: bytes | None = None,
              ctype: str = "application/json") -> None:
        body = raw if raw is not None else json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # CORS: the web viewer may be opened OUTSIDE this origin (editor preview
        # panel, file on disk) and still call the API. Harmless for same-origin
        # clients (the phone uses plain URLSession, no CORS at all).
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_OPTIONS(self):  # CORS preflight (POST /modular sends application/json)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def log_message(self, fmt, *args):  # concise access log
        print(f"[http] {self.address_string()} {fmt % args}", flush=True)

    # -- routes ---------------------------------------------------------------
    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/modular":
            return self._modular()
        if path == "/placement-report":
            return self._placement_report()
        if path != "/jobs":
            return self._send(404, obj={"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, obj={"error": "invalid JSON body"})
        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            return self._send(400, obj={"error": "missing 'prompt'"})
        # mode: "experience" -> Director (multi-station); anything else -> single object.
        mode = data.get("mode")
        if mode not in ("experience", "object", "representation", None):
            mode = None
        prefix = ("exp_" if mode == "experience"
                  else "rep_" if mode == "representation" else "job_")
        job_id = prefix + uuid.uuid4().hex[:8]
        with _LOCK:
            _JOBS[job_id] = {
                "id": job_id, "status": "queued", "stage": "queued",
                "message": None, "prompt": prompt, "mode": mode,
                "client": data.get("client"), "created": time.time(),
                # per-phone concurrency lane (stable id the app sends each launch);
                # falls back to the client's address so distinct phones still split.
                "session": data.get("session") or self.client_address[0],
                "usdz": None, "metadata": None,
            }
        _QUEUE.put(job_id)
        print(f"[job] {job_id} queued (session {_JOBS[job_id]['session']!r}): {prompt!r}",
              flush=True)
        return self._send(200, obj={"id": job_id, "status": "queued"})

    def _modular(self):
        """Synchronous: text or photo -> the modular SPATAIL experience contract
        (agent-composed mechanics). Body: {kind:"text"|"image", text|image(base64),
        note, use_llm}. Returns the v0.5 modular contract directly."""
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, obj={"error": "invalid JSON body"})
        kind = (data.get("kind") or "text").lower()
        use_llm = bool(data.get("use_llm", True))
        # The stream split (tracked vs placed): the phone sets tracked=true when the
        # experience should overlay a REAL object (iOS 27 object tracking). It may
        # name the object and/or a served .referenceobject the runtime can download.
        tracked = bool(data.get("tracked", False))
        tracked_kw = {
            "tracked": tracked,
            "tracked_object_id": (data.get("trackedObjectId") or "").strip() or None,
            "reference_object_url": (data.get("referenceObjectUrl") or "").strip() or None,
        }
        try:
            import sys as _sys
            _d = str(ROOT / "studio" / "director")
            if _d not in _sys.path:
                _sys.path.insert(0, _d)
            import experience as _ex
            if kind == "image":
                import base64 as _b64
                img = data.get("image") or ""
                if "," in img:
                    img = img.split(",", 1)[1]            # strip data: URI prefix
                raw = _b64.b64decode(img)
                contract = _ex.build_from_image(raw, mime=data.get("mime") or "image/jpeg",
                                                note=data.get("note", ""),
                                                question=(data.get("question") or data.get("text") or ""),
                                                use_llm=use_llm, **tracked_kw)
            else:
                text = (data.get("text") or data.get("selectedText") or data.get("prompt") or "").strip()
                if not text:
                    return self._send(400, obj={"error": "missing 'text'"})
                # Iterative project editing: when `prior` (the current scene's
                # subject/summary) is sent, EVOLVE that scene rather than starting
                # over from just the new instruction.
                prior = data.get("prior") or {}
                if prior:
                    psub = (prior.get("subject") or "").strip()
                    psum = (prior.get("summary") or "").strip()
                    subject_hint = f"{psub} — {text}" if psub else text
                    summary = (f"Existing experience: {psum}. Now change it: {text}"
                               if psum else (data.get("surroundingContext") or text))
                    contract = _ex.build_modular_experience(
                        text, kind="text", subject_hint=subject_hint,
                        summary=summary, use_llm=use_llm, **tracked_kw)
                else:
                    contract = _ex.build_modular_experience(
                        text, kind="text", summary=data.get("surroundingContext") or text,
                        use_llm=use_llm, **tracked_kw)
                # Generation gate: only queue an asset build when the primary asset
                # did NOT resolve to a real model. A library hit (incl. every asset
                # the Meshy artist already produced) renders immediately — no job,
                # no credits. This is what makes the library COMPOUND.
                assets_ = contract.get("assets") or []
                primary_a = next((a for a in assets_ if a.get("role") == "primary_object"),
                                 assets_[0] if assets_ else None)
                primary = primary_a["id"] if primary_a else "hero"
                primary_missing = not (primary_a and (primary_a.get("usdzUrl")
                                                      or primary_a.get("glbUrl")))
                subj = (contract.get("understanding") or {}).get("subject") or text
                if primary_missing:
                    contract.setdefault("generation",
                                        {"brief": subj, "subject": subj, "assetId": primary})
            # Real-model generation: enqueue an ASSET build the phone polls and streams
            # in over the placeholder. The worker runs the Meshy artist first
            # (procedural Blender fallback). `asset` carries what the artist needs.
            gen = contract.get("generation") or {}
            if gen.get("brief"):
                gid = "gen_" + uuid.uuid4().hex[:8]
                session = data.get("session") or self.client_address[0]
                assets_ = contract.get("assets") or []
                gen_asset = next((a for a in assets_ if a["id"] == gen.get("assetId")),
                                 assets_[0] if assets_ else {})
                with _LOCK:
                    _JOBS[gid] = {"id": gid, "status": "queued", "stage": "queued",
                                  "message": None, "prompt": gen["brief"], "mode": "object",
                                  "client": "modular", "created": time.time(),
                                  "session": session, "usdz": None, "metadata": None,
                                  "asset": {
                                      "assetId": gen.get("assetId"),
                                      "subject": gen.get("subject") or "",
                                      "scaleMeters": gen_asset.get("scaleMeters"),
                                      "category": (contract.get("understanding") or {}).get("domain"),
                                      # story→asset brief for THIS asset (slice 1): the
                                      # parts the lesson will point at, so the producer
                                      # finds them + bakes them as addressable regions.
                                      "assetRequirements": (contract.get("assetRequirements")
                                                            or {}).get(gen.get("assetId")),
                                  }}
                _QUEUE.put(gid)
                contract["generationJobId"] = gid
                print(f"[modular] queued asset build {gid} for {gen.get('subject')!r}: "
                      f"{gen['brief'][:90]!r}", flush=True)
            # Attach the v0.6 Scene Contract (content/placement/brand/logic) additively
            # so newer phones get the game-manager trigger graph; old builds ignore it.
            try:
                import scene_contract as _sc
                contract["sceneContract"] = _sc.to_scene_contract(contract)
            except Exception as _sce:  # noqa: BLE001
                print(f"[modular] sceneContract skip: {_sce}", flush=True)
            # LIVE_BRAIN_SPEC §2.1/§2.3 — schema truth (warn-only, never blocks) +
            # the placement trace, persisted BEFORE returning so every contract on
            # the wire is attributable at /traces/{experienceId}.
            if trace_store is not None:
                validation = trace_store.validate_contract(
                    contract, contract.get("sceneContract"))
                contract["schemaValidation"] = validation
                if not validation.get("ok", True):
                    errs = validation.get("errors") or []
                    print(f"[modular] schema WARN ({len(errs)}): "
                          f"{'; '.join(errs[:3])}", flush=True)
                try:
                    eid = contract.get("experienceId") or ""
                    if eid:
                        contract["traceUrl"] = f"/traces/{eid}"
                        trace = {"experienceId": eid,
                                 "createdAt": trace_store.iso_now(),
                                 "prompt": (contract.get("source") or {}).get("input") or "",
                                 "contract": contract}
                        if contract.get("sceneContract"):
                            trace["sceneContract"] = contract["sceneContract"]
                        trace["schemaValidation"] = validation
                        trace["reports"] = []
                        trace_store.save(trace)
                except Exception as _te:  # noqa: BLE001
                    print(f"[modular] trace persist skip: {_te}", flush=True)
            print(f"[modular] {kind} -> {contract.get('title')!r} "
                  f"({contract.get('composer')}, {len(contract.get('beats', []))} beats, "
                  f"{len(contract.get('mechanicsUsed', []))} mechanics)", flush=True)
            return self._send(200, obj=contract)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            return self._send(500, obj={"error": "modular build failed", "detail": str(e)[:300]})

    def _placement_report(self):
        """Client attribution (LIVE_BRAIN_SPEC §2.2): the runtime posts what it
        actually DID with a contract (solver inputs, plan, final transforms);
        appended into the experience's trace.reports[] for /traces/view."""
        if trace_store is None:
            return self._send(503, obj={"error": "trace store unavailable"})
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            report = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, obj={"error": "invalid JSON body"})
        if not isinstance(report, dict):
            return self._send(400, obj={"error": "body must be a JSON object"})
        eid = report.get("experienceId")
        eid = eid.strip() if isinstance(eid, str) else ""
        client = report.get("client")
        if not eid:
            return self._send(400, obj={"error": "missing 'experienceId'"})
        if not client or not isinstance(client, str):
            return self._send(400, obj={"error": "missing 'client'"})
        report.setdefault("receivedAt", time.time())
        try:
            trace = trace_store.append_report(eid, report)
        except Exception as exc:  # noqa: BLE001
            return self._send(500, obj={"error": "trace append failed",
                                        "detail": str(exc)[:200]})
        if trace is None:
            return self._send(404, obj={"error": "unknown experience",
                                        "experienceId": eid})
        n = len(trace.get("reports") or [])
        print(f"[trace] {eid}: placement report #{n} from {client!r}", flush=True)
        return self._send(200, obj={"ok": True, "experienceId": eid,
                                    "reportCount": n})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlsplit(self.path).path

        if path == "/health":
            bridge = blender_bridge.ping()
            with _LOCK:
                njobs = len(_JOBS)
            return self._send(200, obj={
                "ok": True, "service": "spatail-gen-job-server",
                "contract": CONTRACT_VERSION,
                "blender_bridge": bool(bridge),
                "blender": (bridge or {}).get("blender"),
                "jobs": njobs, "artifacts_dir": str(ARTIFACTS),
            })
        # "/" serves the web viewer (flat asset+explanation preview) further down.

        # ── Placement traces (LIVE_BRAIN_SPEC §2.2): list / file / attribution
        # page. /traces/view must match before the /traces/{experienceId} prefix. ──
        if path in ("/traces", "/traces/"):
            if trace_store is None:
                return self._send(503, obj={"error": "trace store unavailable"})
            return self._send(200, obj=trace_store.list_traces())

        if path == "/traces/view":
            page = Path(__file__).resolve().parent / "traces_view.html"
            if not page.is_file():
                return self._send(404, obj={"error": "traces view not installed"})
            return self._send(200, raw=page.read_bytes(),
                              ctype="text/html; charset=utf-8")

        if path.startswith("/traces/qa/"):
            # QA/verify render for a generation job (the attribution page's Asset
            # column): the job's metadata names a PC-side render under studio/out
            # (vision verify.png / meshy_normalize *_qa.png) that no artifact route
            # serves. Resolve it FROM the metadata (server-written, never client
            # input) and serve it read-only, locked inside studio/out.
            gid = Path(unquote(path[len("/traces/qa/"):])).name
            meta_fp = ARTIFACTS / f"{gid}_metadata.json"
            if not gid or not meta_fp.is_file():
                return self._send(404, obj={"error": "unknown job", "id": gid})
            try:
                meta = json.loads(meta_fp.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                return self._send(404, obj={"error": "metadata unreadable", "id": gid})
            img = str((meta.get("normalizeQA") or {}).get("image") or "")
            base = (ROOT / "studio" / "out").resolve()
            fp = Path(img).resolve() if img else None
            if fp is None or not str(fp).startswith(str(base)) or not fp.is_file():
                return self._send(404, obj={"error": "qa render not found", "id": gid})
            ext = fp.suffix.lower()
            ctype = ("image/png" if ext == ".png"
                     else "image/jpeg" if ext in (".jpg", ".jpeg")
                     else "application/octet-stream")
            return self._send(200, raw=fp.read_bytes(), ctype=ctype)

        if path.startswith("/traces/"):
            if trace_store is None:
                return self._send(503, obj={"error": "trace store unavailable"})
            eid = unquote(path[len("/traces/"):]).strip("/")
            trace = trace_store.read(eid)
            if trace is None:
                return self._send(404, obj={"error": "unknown experience",
                                            "experienceId": eid})
            return self._send(200, obj=trace)

        if path.startswith("/mechanics/"):
            base = (ROOT / "public" / "mechanics").resolve()
            fp = (base / unquote(path)[len("/mechanics/"):]).resolve()
            if not str(fp).startswith(str(base)) or not fp.is_file():
                return self._send(404, obj={"error": "not found", "path": path})
            return self._send(200, raw=fp.read_bytes(), ctype="application/json")

        if path.startswith("/jobs/"):
            job_id = unquote(path[len("/jobs/"):]).strip("/")
            job = _snapshot(job_id)
            if not job:
                return self._send(404, obj={"error": "unknown job", "id": job_id})
            out = {
                "id": job["id"], "status": job["status"],
                "stage": job.get("stage"), "message": job.get("message"),
            }
            # Determinate build-progress (dev tool): step k / N + percent. Additive —
            # old clients ignore these and keep showing the indeterminate spinner.
            if job.get("stageTotal"):
                out["stageIndex"] = job.get("stageIndex", 0)
                out["stageTotal"] = job["stageTotal"]
                out["percent"] = round(100.0 * out["stageIndex"] / max(job["stageTotal"], 1))
            # While still waiting for a lane, tell the phone its place in line so it
            # shows real progress ("queued (2 ahead)") instead of a blind countdown.
            if job["status"] == "queued":
                ahead = _ahead_of(job_id)
                out["queuePosition"] = ahead
                out["stage"] = f"queued ({ahead} ahead)" if ahead else "queued"
            if job["status"] == "done":
                if job.get("representation"):
                    # Representation job: plan + manifest + runtime + progressive
                    out["runtime_url"] = f"/artifacts/{job['runtime']}"
                    out["delivery_url"] = f"/artifacts/{job['delivery']}"
                    out["progressive_url"] = f"/artifacts/{job['progressive']}"
                    out["plan_url"] = f"/artifacts/{job.get('plan_artifact')}"
                    out["title"] = job.get("title")
                    out["stations"] = job.get("stations")
                elif job.get("experience"):
                    # Director job: point the client at the Experience Spec
                    out["experience_url"] = f"/artifacts/{job['experience']}"
                    out["usdz_base"] = "/artifacts/"
                    out["stations"] = job.get("stations")
                    out["title"] = job.get("title")
                else:
                    glb_exists = (ARTIFACTS / f"{job['id']}.glb").exists()
                    manifest_exists = (ARTIFACTS / f"{job['id']}_manifest.json").exists()
                    # Android XR consumes glTF/GLB natively; iOS/RealityKit consumes
                    # USDZ. For android clients GLB + manifest are the primary (and only)
                    # model fields — USDZ is intentionally omitted (Master File pivot
                    # 2026-06). Until every builder guarantees a GLB twin (the Meshy
                    # artist already does), an android build with no GLB falls back to
                    # the USDZ fields rather than returning a broken /artifacts/None.
                    if _client_platform(job) == "android" and glb_exists:
                        out["glb_url"] = f"/artifacts/{job['id']}.glb"
                        if manifest_exists:
                            out["manifest_url"] = f"/artifacts/{job['id']}_manifest.json"
                        if job.get("metadata"):
                            out["metadata_url"] = f"/artifacts/{job['metadata']}"
                    else:
                        # iOS / default: USDZ primary, GLB twin + manifest additive.
                        if job.get("usdz"):
                            out["usdz_url"] = f"/artifacts/{job['usdz']}"
                        if job.get("metadata"):
                            out["metadata_url"] = f"/artifacts/{job['metadata']}"
                        # GLB twin (the Meshy artist writes one) — the web viewer's format
                        if glb_exists:
                            out["glb_url"] = f"/artifacts/{job['id']}.glb"
                        # component manifest (parts/roles/motion) if the build emitted one —
                        # lets the runtime bind named parts (explode/tap a part, motion).
                        if manifest_exists:
                            out["manifest_url"] = f"/artifacts/{job['id']}_manifest.json"
            return self._send(200, obj=out)

        if path.startswith("/artifacts/"):
            fname = Path(unquote(path[len("/artifacts/"):])).name  # basename only
            fp = ARTIFACTS / fname
            if not fname or not fp.exists() or not fp.is_file():
                return self._send(404, obj={"error": "artifact not found", "file": fname})
            if fname.endswith(".usdz"):
                ctype = "model/vnd.usdz+zip"
            elif fname.endswith(".json"):
                ctype = "application/json"
            else:
                ctype = "application/octet-stream"
            body = fp.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'inline; filename="{fname}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return None

        # ── Asset Factory: list + serve normalized ingested assets for the iOS
        # factory browser (a different subsystem from the generative library) ──
        if path == "/factory/index":
            return self._send(200, obj=_factory_index())

        if path.startswith("/factory/file/"):
            base = (ROOT / "assets_processed").resolve()
            fp = (base / unquote(path)[len("/factory/file/"):]).resolve()
            if not str(fp).startswith(str(base)) or not fp.is_file():
                return self._send(404, obj={"error": "factory file not found", "path": path})
            ext = fp.suffix.lower()
            ctype = ("model/vnd.usdz+zip" if ext == ".usdz"
                     else "model/gltf-binary" if ext == ".glb"
                     else "image/png" if ext == ".png"
                     else "application/json" if ext == ".json"
                     else "application/octet-stream")
            return self._send(200, raw=fp.read_bytes(), ctype=ctype)

        # ── Web viewer: a FLAT preview of the content pipeline (model + the
        # explanation contract) for fast feedback — the product stays MR on the
        # phone. One static page + a library index; everything else reuses the
        # exact same /modular, /jobs and /assets endpoints the phone uses. ──
        if path in ("/", "/web", "/web/"):
            page = ROOT / "public" / "webviewer" / "index.html"
            if not page.is_file():
                return self._send(404, obj={"error": "webviewer not installed"})
            return self._send(200, raw=page.read_bytes(), ctype="text/html; charset=utf-8")

        if path == "/web/assets":
            return self._send(200, obj=_webviewer_assets())

        # ── Trackables: the reference-object library for the TRACKED stream.
        # .referenceobject files are trained on the Mac (Create ML / `xcrun createml
        # objecttracker`), dropped into public/assets/spatail-trackables/, and
        # downloaded by the phone at runtime (ARReferenceObject(archiveURL:) loads
        # any local file URL — post-compile delivery, no app rebuild). ──
        if path == "/trackables":
            return self._send(200, obj=_trackables_index())

        if path.startswith("/trackables/"):
            base = (ROOT / "public" / "assets" / "spatail-trackables").resolve()
            fp = (base / unquote(path)[len("/trackables/"):]).resolve()
            if not str(fp).startswith(str(base)) or not fp.is_file():
                return self._send(404, obj={"error": "trackable not found", "path": path})
            return self._send(200, raw=fp.read_bytes(), ctype="application/octet-stream")

        # Serve the starter asset library (cached GLB/USDZ) at the SAME path the
        # manifests reference, so the iOS app and web viewer fetch real cached assets.
        if path.startswith("/assets/spatail-library/"):
            base = (ROOT / "public" / "assets" / "spatail-library").resolve()
            fp = (base / unquote(path)[len("/assets/spatail-library/"):]).resolve()
            if not str(fp).startswith(str(base)) or not fp.is_file():
                return self._send(404, obj={"error": "asset not found", "path": path})
            ext = fp.suffix.lower()
            ctype = ("model/vnd.usdz+zip" if ext == ".usdz"
                     else "model/gltf-binary" if ext == ".glb"
                     else "application/json" if ext == ".json"
                     else "application/octet-stream")
            return self._send(200, raw=fp.read_bytes(), ctype=ctype)

        return self._send(404, obj={"error": "not found"})


def _webviewer_assets() -> dict:
    """The Meshy library for the web viewer: one entry per slug, preferring the
    animated cut, then the decimated one, then full-res. GLB for <model-viewer>."""
    root = (ROOT / "public" / "assets" / "spatail-library" / "meshy").resolve()
    base = "/assets/spatail-library/meshy"
    out: dict[str, dict] = {}
    if root.is_dir():
        for glb in sorted(root.glob("*.glb")):
            stem = glb.stem
            slug = stem[:-5] if stem.endswith("_anim") else (
                stem[:-3] if stem.endswith("_ar") else stem)
            rank = 2 if stem.endswith("_anim") else (1 if stem.endswith("_ar") else 0)
            cur = out.get(slug)
            if cur is None or rank > cur["_rank"]:
                usdz = glb.with_suffix(".usdz")
                out[slug] = {"_rank": rank, "id": slug,
                             "name": slug.replace("_", " ").title(),
                             "glb": f"{base}/{glb.name}",
                             "usdz": f"{base}/{usdz.name}" if usdz.exists() else "",
                             "animated": stem.endswith("_anim")}
    assets = [{k: v for k, v in a.items() if k != "_rank"} for a in out.values()]
    return {"assets": sorted(assets, key=lambda a: a["id"]), "count": len(assets)}


def _trackables_index() -> dict:
    """List the reference-object library (TRACKED stream anchors).

    Each entry: {id, name, url, tracking, subjects}. `tracking` declares the
    runtime lane — "detection" (stationary, low power) vs "tracking" (handheld,
    per-frame high rate). An optional sidecar <id>.json refines name/tracking/
    subjects; without one, sane defaults apply. Drop new .referenceobject files
    in public/assets/spatail-trackables/ — no server restart needed (scanned per
    request; the dir is tiny)."""
    root = (ROOT / "public" / "assets" / "spatail-trackables").resolve()
    items = []
    if root.is_dir():
        for ro in sorted(root.glob("*.referenceobject")):
            rid = ro.stem
            meta = {}
            side = root / f"{rid}.json"
            if side.is_file():
                try:
                    meta = json.loads(side.read_text(encoding="utf-8")) or {}
                except (ValueError, OSError):
                    meta = {}
            items.append({
                "id": rid,
                "name": meta.get("name", rid.replace("_", " ").title()),
                "url": f"/trackables/{ro.name}",
                "bytes": ro.stat().st_size,
                "tracking": meta.get("tracking", "detection"),
                "subjects": meta.get("subjects", [rid.replace("_", " ")]),
            })
    return {"trackables": items, "count": len(items)}


def _factory_index() -> dict:
    """List successfully-processed AI Asset Factory assets for the iOS browser.

    Reads the factory's own assets_processed/<id>/asset_manifest.json files
    directly (stdlib only) — it deliberately does NOT import the top-level
    asset_factory package, which would shadow studio/asset_factory on sys.path.
    """
    root = (ROOT / "assets_processed").resolve()
    assets = []
    if root.is_dir():
        for man in sorted(root.glob("*/asset_manifest.json")):
            try:
                m = json.loads(man.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if m.get("status") != "success":
                continue
            aid = m.get("asset_id") or man.parent.name
            d = man.parent
            exported = m.get("exported_files", {}) or {}
            has_usdz = bool(exported.get("usdz")) and (d / f"{aid}.normalized.usdz").is_file()
            has_prev = (d / "preview.png").is_file()
            assets.append({
                "id": aid,
                "title": aid.replace("_", " ").title(),
                "bounds_mode": m.get("bounds_mode"),
                "origin_mode": m.get("origin_mode"),
                "target_bounds_m": m.get("target_bounds_m", {}),
                "final_bounds_m": m.get("final_bounds_m", {}),
                "triangle_count": m.get("triangle_count", 0),
                "unit_system": m.get("unit_system", "meters"),
                "glb_url": f"/factory/file/{aid}/{aid}.normalized.glb",
                "usdz_url": f"/factory/file/{aid}/{aid}.normalized.usdz" if has_usdz else "",
                "preview_url": f"/factory/file/{aid}/preview.png" if has_prev else "",
                "has_usdz": has_usdz,
            })
    return {"assets": assets, "count": len(assets)}


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main() -> None:
    ap = argparse.ArgumentParser(description="SPATAIL generative AR job server")
    ap.add_argument("--host", default="0.0.0.0",
                    help="bind address (default 0.0.0.0 = all interfaces incl. Tailscale)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--no-keep-awake", action="store_true",
                    help="don't prevent Windows sleep/display-off while serving")
    ap.add_argument("--no-watchdog", action="store_true",
                    help="don't auto-relaunch Blender if its bridge goes down")
    args = ap.parse_args()

    if not args.no_keep_awake:
        _keep_awake()
    if not args.no_watchdog:
        threading.Thread(target=_blender_watchdog, daemon=True,
                         name="blender-watchdog").start()

    # Scheduler: intake drains the queue into pending; dispatch starts builds on
    # their own threads under the (global, per-session) caps — per-phone concurrency.
    threading.Thread(target=_intake, daemon=True, name="gen-intake").start()
    threading.Thread(target=_dispatch_loop, daemon=True, name="gen-dispatch").start()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True

    bridge = blender_bridge.ping()
    host_label = socket.gethostname().lower()
    print("=" * 64, flush=True)
    print(f"SPATAIL generative AR job server  (contract v{CONTRACT_VERSION})", flush=True)
    print(f"  bind            : {args.host}:{args.port}", flush=True)
    print(f"  local           : http://127.0.0.1:{args.port}", flush=True)
    print(f"  lan             : http://{_lan_ip()}:{args.port}", flush=True)
    print(f"  tailscale (MagicDNS, once up): http://{host_label}.<tailnet>.ts.net:{args.port}",
          flush=True)
    print(f"  artifacts       : {ARTIFACTS}", flush=True)
    print(f"  Blender bridge  : {'UP - ' + (bridge or {}).get('blender', '') if bridge else 'DOWN (open Blender + start MCP add-on)'}",
          flush=True)
    print(f"  keep-awake      : {'off' if args.no_keep_awake else 'on'}", flush=True)
    print(f"  blender watchdog: {'off' if args.no_watchdog else 'on (relaunch if bridge drops)'}",
          flush=True)
    iso = ISOLATED and headless_build.available()
    print(f"  build isolation : {'per-phone headless Blender' if iso else 'shared live spine'}"
          f"  (global cap {GLOBAL_CAP}, per-session {SESSION_CAP})", flush=True)
    if ISOLATED and not headless_build.available():
        print(f"  NOTE: isolation requested but Blender exe/driver missing — using shared spine",
              flush=True)
    print("=" * 64, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] shutting down", flush=True)
        httpd.shutdown()


if __name__ == "__main__":
    main()
