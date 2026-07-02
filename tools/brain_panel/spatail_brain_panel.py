"""SPATAIL Brain Panel — desktop control panel for the PC fusion-brain stack.

One window to run, stop, and understand everything the phone talks to:

  ENGINE   pipeline/server/spatail_vision_engine.py (frame WS :8798,
           debug view :8799, fusion replan → experience.delta)
  OLLAMA   the local VLM server (:11434) + which models are loaded and
           whether they're on GPU or spilling to CPU
  GPU      VRAM pressure (the thing that decides 0.6 s vs 20 s latency)
  NETWORK  LAN IP, the exact ws:// URL to type into the phone, firewall rules
  SELFTEST one button that plays a fake phone: sends the sample room over
           the real WebSocket and checks the brain answers with foam placement

Design notes:
  - stdlib only (tkinter/subprocess/socket/urllib). The self-test uses the
    `websockets` package if present (it is on this PC) and degrades to a
    clear message if not.
  - The engine runs as a DETACHED child logging to a file, so the panel can
    close and reopen and re-attach; stop works by port→PID discovery, which
    also cleans up engines the panel didn't start.
  - The repo root defaults to the checkout this file lives in
    (…/tools/brain_panel/ → repo root), so the panel always runs the engine
    from a branch that actually HAS the engine — the exact failure mode of
    running from C:\SPATAIL_MAX while the branch is in a worktree.

Run:  double-click SPATAIL_Brain_Panel.bat next to this file
      (or: python spatail_brain_panel.py; --selftest-cli for a headless probe)
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty

APP_NAME = "SPATAIL Brain Panel"
VERSION = "0.1"

FRAME_PORT = 8798
DEBUG_PORT = 8799
OLLAMA_PORT = 11434

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

APPDATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "SPATAIL"
LOG_DIR = APPDATA_DIR / "logs"
CONFIG_PATH = APPDATA_DIR / "brain_panel_config.json"
ENGINE_LOG = LOG_DIR / "vision_engine.log"
OLLAMA_LOG = LOG_DIR / "ollama_serve.log"

FIREWALL_RULES = ["SPATAIL vision uplink 8798", "SPATAIL vision debug 8799"]

# Colors — matches the SPATAIL debug-view palette.
C_BG = "#0c0c14"
C_PANEL = "#15151f"
C_TEXT = "#e7e7ef"
C_DIM = "#7c7c92"
C_ACCENT = "#9db4ff"
C_OK = "#3ddc84"
C_BAD = "#ff5470"
C_WARN = "#ffc24b"


# ────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────

def default_repo_root() -> Path:
    """The checkout this panel lives in: tools/brain_panel/x.py → repo root."""
    here = Path(__file__).resolve()
    root = here.parents[2]
    return root


@dataclass
class Config:
    repo_root: str = str(default_repo_root())
    model: str = "qwen2.5vl:3b"
    test_dir: str = ""
    use_test_dir: bool = False
    vlm_timeout: int = 90
    extra_args: str = ""
    data: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Config":
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg = cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
            return cfg
        except Exception:
            return cls()

    def save(self):
        APPDATA_DIR.mkdir(parents=True, exist_ok=True)
        body = {k: getattr(self, k) for k in
                ("repo_root", "model", "test_dir", "use_test_dir",
                 "vlm_timeout", "extra_args")}
        CONFIG_PATH.write_text(json.dumps(body, indent=2), encoding="utf-8")


# ────────────────────────────────────────────────────────────────────────
# Probes (no GUI — also used by --selftest-cli)
# ────────────────────────────────────────────────────────────────────────

def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_json(url: str, timeout: float = 2.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def lan_ip() -> str:
    """The outbound-facing LAN IP (what the phone should dial)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def pids_listening_on(port: int) -> list[int]:
    """Windows netstat parse → PIDs listening on a TCP port."""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, timeout=8,
            creationflags=CREATE_NO_WINDOW,
        ).stdout
    except Exception:
        return []
    pids = set()
    needle = f":{port}"
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
            local = parts[1]
            if local.endswith(needle):
                try:
                    pids.add(int(parts[4]))
                except ValueError:
                    pass
    return sorted(pids)


def kill_pid_tree(pid: int) -> str:
    r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, text=True, timeout=15,
                       creationflags=CREATE_NO_WINDOW)
    return (r.stdout or r.stderr or "").strip()


def kill_by_name(*names: str) -> list[str]:
    out = []
    for n in names:
        r = subprocess.run(["taskkill", "/IM", n, "/T", "/F"],
                           capture_output=True, text=True, timeout=15,
                           creationflags=CREATE_NO_WINDOW)
        line = (r.stdout or r.stderr or "").strip().splitlines()
        out.extend(line[:2])
    return out


def gpu_status() -> dict | None:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6,
            creationflags=CREATE_NO_WINDOW)
        used, total, util = [x.strip() for x in r.stdout.strip().split(",")]
        return {"used": int(used), "total": int(total), "util": int(util)}
    except Exception:
        return None


def firewall_rule_exists(name: str) -> bool:
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
            capture_output=True, text=True, timeout=8,
            creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0 and "No rules match" not in r.stdout
    except Exception:
        return False


def git_branch(repo: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=8,
                           creationflags=CREATE_NO_WINDOW)
        return r.stdout.strip() or "?"
    except Exception:
        return "?"


def ollama_exe() -> Path | None:
    p = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return p if p.exists() else None


def snapshot(cfg: Config) -> dict:
    """One status sweep across the whole stack."""
    repo = Path(cfg.repo_root)
    engine_file = repo / "pipeline" / "server" / "spatail_vision_engine.py"
    engine_up = port_open(DEBUG_PORT)
    state = http_json(f"http://127.0.0.1:{DEBUG_PORT}/state.json") if engine_up else None
    contract = http_json(f"http://127.0.0.1:{DEBUG_PORT}/contract") if engine_up else None

    ollama_up = port_open(OLLAMA_PORT)
    tags = http_json(f"http://127.0.0.1:{OLLAMA_PORT}/api/tags") if ollama_up else None
    ps = http_json(f"http://127.0.0.1:{OLLAMA_PORT}/api/ps") if ollama_up else None

    return {
        "time": datetime.now().strftime("%H:%M:%S"),
        "repo_ok": engine_file.exists(),
        "branch": git_branch(repo) if repo.exists() else "?",
        "engine_up": engine_up,
        "engine_pids": pids_listening_on(FRAME_PORT) + pids_listening_on(DEBUG_PORT),
        "state": state,
        "contract": contract,
        "ollama_up": ollama_up,
        "ollama_models": [m.get("name") for m in (tags or {}).get("models", [])],
        "ollama_loaded": (ps or {}).get("models", []),
        "gpu": gpu_status(),
        "lan_ip": lan_ip(),
        "firewall": {n: firewall_rule_exists(n) for n in FIREWALL_RULES},
    }


# ────────────────────────────────────────────────────────────────────────
# Actions
# ────────────────────────────────────────────────────────────────────────

def start_engine(cfg: Config) -> str:
    repo = Path(cfg.repo_root)
    engine = repo / "pipeline" / "server" / "spatail_vision_engine.py"
    if not engine.exists():
        return (f"engine file not found:\n  {engine}\n"
                f"→ Repo path must be a checkout of branch "
                f"claude/interesting-goldwasser-35d3c5 (Browse… to fix).")
    if port_open(DEBUG_PORT):
        return "engine already running (port 8799 busy) — use Restart."

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe":          # engine wants a real python
        py = py.with_name("python.exe")

    args = [str(py), str(engine), "--vlm-model", cfg.model,
            "--vlm-timeout", str(cfg.vlm_timeout)]
    if cfg.use_test_dir and cfg.test_dir:
        args += ["--test-dir", cfg.test_dir]
    if cfg.extra_args.strip():
        args += cfg.extra_args.split()

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"             # engine logs contain → / ×
    env["PYTHONUTF8"] = "1"

    log = open(ENGINE_LOG, "a", encoding="utf-8")
    log.write(f"\n===== {datetime.now().isoformat()} start: {' '.join(args)}\n")
    log.flush()
    subprocess.Popen(args, cwd=str(repo), env=env,
                     stdout=log, stderr=subprocess.STDOUT,
                     creationflags=CREATE_NO_WINDOW)
    return f"engine starting — {cfg.model}" + (
        f" + test-dir {cfg.test_dir}" if cfg.use_test_dir and cfg.test_dir else "")


def stop_engine() -> str:
    pids = sorted(set(pids_listening_on(FRAME_PORT) + pids_listening_on(DEBUG_PORT)))
    if not pids:
        return "engine not running."
    lines = [kill_pid_tree(p) for p in pids]
    return "stopped: " + "; ".join(lines)


def start_ollama() -> str:
    exe = ollama_exe()
    if exe is None:
        return "ollama.exe not found — install Ollama first."
    if port_open(OLLAMA_PORT):
        return "Ollama already running."
    env = dict(os.environ)
    env.setdefault("OLLAMA_NUM_PARALLEL", "1")
    env.setdefault("OLLAMA_CONTEXT_LENGTH", "4096")
    env.setdefault("OLLAMA_MAX_LOADED_MODELS", "1")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(OLLAMA_LOG, "a", encoding="utf-8")
    log.write(f"\n===== {datetime.now().isoformat()} ollama serve\n")
    log.flush()
    subprocess.Popen([str(exe), "serve"], env=env,
                     stdout=log, stderr=subprocess.STDOUT,
                     creationflags=CREATE_NO_WINDOW)
    return "Ollama starting (single-slot config)…"


def stop_ollama() -> str:
    lines = kill_by_name("ollama app.exe", "ollama.exe", "llama-server.exe")
    return "; ".join(l for l in lines if l) or "nothing to stop."


def kill_gpu_orphans() -> str:
    """The 8 GB card's nemesis: leftover llama-server runners pinning VRAM."""
    lines = kill_by_name("llama-server.exe")
    return "; ".join(l for l in lines if l) or "no llama-server orphans."


def fix_firewall() -> str:
    """Adds both inbound rules via an elevated PowerShell (UAC prompt)."""
    inner = (
        "New-NetFirewallRule -DisplayName 'SPATAIL vision uplink 8798' "
        "-Direction Inbound -Action Allow -Protocol TCP -LocalPort 8798 -Profile Private; "
        "New-NetFirewallRule -DisplayName 'SPATAIL vision debug 8799' "
        "-Direction Inbound -Action Allow -Protocol TCP -LocalPort 8799 -Profile Private"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-Command',\"{inner}\""],
            timeout=60, creationflags=CREATE_NO_WINDOW)
        return "firewall fix launched — approve the UAC prompt."
    except Exception as exc:
        return f"firewall fix failed: {exc}"


def run_selftest(cfg: Config) -> str:
    """Play a fake phone: send the sample room over ws://:8798 and verify the
    brain answers with surface_edge/surface_corner placement."""
    repo = Path(cfg.repo_root)
    room_file = repo / "pipeline" / "spatail" / "samples" / "sample_ios_room.json"
    if not room_file.exists():
        return "FAIL — sample room missing (wrong repo/branch?)"
    if not port_open(FRAME_PORT):
        return "FAIL — engine not running (start it first)"
    try:
        from websockets.sync.client import connect  # deferred import
    except Exception:
        return "SKIP — python package `websockets` not available"

    try:
        room = json.loads(room_file.read_text(encoding="utf-8"))
        msg = {"type": "room.update", "payload": {
            "room": room,
            "pose": {"position": [0, 1.5, 1.6], "forward": [0, -0.45, -0.89]},
            "debugIdentification": {"primary": "table",
                                    "detections": [{"label": "table", "confidence": 0.93}]},
        }}
        with connect(f"ws://127.0.0.1:{FRAME_PORT}/v1/vision",
                     max_size=8 * 1024 * 1024) as ws:
            ws.send(json.dumps(msg))
            deadline = time.time() + 20
            while time.time() < deadline:
                raw = ws.recv(timeout=max(0.1, deadline - time.time()))
                data = json.loads(raw)
                if data.get("type") != "experience.delta":
                    continue
                exp = data["payload"]["experience"]
                els = exp["spatialElements"]
                edges = [e for e in els if e["placement"]["kind"] == "surface_edge"]
                corners = [e for e in els if e["placement"]["kind"] == "surface_corner"]
                if edges and corners:
                    return (f"PASS — v{data['payload']['version']}: "
                            f"{len(edges)} edges + {len(corners)} corners · "
                            f"{exp.get('reasoningSummary', '')[:90]}")
                return f"FAIL — delta arrived but no surface placements ({len(els)} elements)"
        return "FAIL — no experience.delta within 20 s"
    except Exception as exc:
        return f"FAIL — {type(exc).__name__}: {exc}"


# ────────────────────────────────────────────────────────────────────────
# GUI
# ────────────────────────────────────────────────────────────────────────

def run_gui():
    import tkinter as tk
    from tkinter import filedialog

    cfg = Config.load()
    status_q: Queue = Queue()
    note_q: Queue = Queue()

    root = tk.Tk()
    root.title(f"{APP_NAME} v{VERSION}")
    root.configure(bg=C_BG)
    root.geometry("980x760")
    root.minsize(880, 640)

    mono = ("Consolas", 9)
    ui = ("Segoe UI", 10)
    ui_b = ("Segoe UI", 10, "bold")
    big = ("Segoe UI", 13, "bold")

    def lbl(parent, text="", fg=C_TEXT, font=ui, **kw):
        return tk.Label(parent, text=text, fg=fg, bg=parent["bg"], font=font,
                        anchor="w", **kw)

    def btn(parent, text, cmd, color=C_ACCENT):
        return tk.Button(parent, text=text, command=cmd, font=ui_b,
                         fg="#0c0c14", bg=color, activebackground=C_TEXT,
                         relief="flat", padx=12, pady=4, cursor="hand2")

    def note(text):
        note_q.put(text)

    def in_thread(fn, *a):
        threading.Thread(target=lambda: note(fn(*a)), daemon=True).start()

    # ── header ─────────────────────────────────────────────────────────
    header = tk.Frame(root, bg=C_PANEL)
    header.pack(fill="x")
    lbl(header, "SPATAIL BRAIN PANEL", fg=C_ACCENT, font=("Segoe UI", 15, "bold")
        ).pack(side="left", padx=14, pady=10)
    phone_var = tk.StringVar(value="ws://…:8798/v1/vision")
    lbl(header, "phone →", fg=C_DIM).pack(side="left", padx=(20, 4))
    phone_entry = tk.Entry(header, textvariable=phone_var, font=mono, width=34,
                           fg=C_TEXT, bg=C_BG, insertbackground=C_TEXT,
                           relief="flat", readonlybackground=C_BG, state="readonly")
    phone_entry.pack(side="left", pady=10)

    def copy_phone():
        root.clipboard_clear()
        root.clipboard_append(phone_var.get())
        note("phone URL copied to clipboard")
    btn(header, "Copy", copy_phone).pack(side="left", padx=8)

    # ── service cards ──────────────────────────────────────────────────
    cards = tk.Frame(root, bg=C_BG)
    cards.pack(fill="x", padx=12, pady=(10, 4))
    cards.columnconfigure((0, 1, 2), weight=1, uniform="cards")

    def card(col, title):
        f = tk.Frame(cards, bg=C_PANEL, padx=12, pady=10)
        f.grid(row=0, column=col, sticky="nsew", padx=4)
        head = tk.Frame(f, bg=C_PANEL)
        head.pack(fill="x")
        dot = tk.Label(head, text="●", fg=C_DIM, bg=C_PANEL, font=big)
        dot.pack(side="left")
        lbl(head, title, font=big).pack(side="left", padx=6)
        body = lbl(f, "…", fg=C_DIM, font=mono, justify="left")
        body.pack(fill="x", pady=(6, 0))
        return dot, body

    eng_dot, eng_body = card(0, "ENGINE")
    oll_dot, oll_body = card(1, "OLLAMA")
    gpu_dot, gpu_body = card(2, "GPU / NET")

    # ── controls ───────────────────────────────────────────────────────
    ctrl = tk.Frame(root, bg=C_PANEL, padx=12, pady=10)
    ctrl.pack(fill="x", padx=12, pady=4)

    row1 = tk.Frame(ctrl, bg=C_PANEL); row1.pack(fill="x")
    lbl(row1, "Repo", fg=C_DIM, width=6).pack(side="left")
    repo_var = tk.StringVar(value=cfg.repo_root)
    tk.Entry(row1, textvariable=repo_var, font=mono, fg=C_TEXT, bg=C_BG,
             insertbackground=C_TEXT, relief="flat").pack(
        side="left", fill="x", expand=True, ipady=3)

    def browse_repo():
        d = filedialog.askdirectory(initialdir=repo_var.get() or "C:\\")
        if d:
            repo_var.set(d)
            apply_cfg()
    btn(row1, "Browse…", browse_repo, C_DIM).pack(side="left", padx=6)
    branch_lbl = lbl(row1, "", fg=C_DIM, font=mono)
    branch_lbl.pack(side="left", padx=4)

    row2 = tk.Frame(ctrl, bg=C_PANEL); row2.pack(fill="x", pady=(8, 0))
    lbl(row2, "Model", fg=C_DIM, width=6).pack(side="left")
    model_var = tk.StringVar(value=cfg.model)
    model_menu = tk.OptionMenu(row2, model_var, cfg.model)
    model_menu.configure(font=mono, fg=C_TEXT, bg=C_BG, relief="flat",
                         highlightthickness=0, activebackground=C_PANEL)
    model_menu.pack(side="left")

    test_var = tk.BooleanVar(value=cfg.use_test_dir)
    tk.Checkbutton(row2, text="test-dir", variable=test_var, font=ui,
                   fg=C_TEXT, bg=C_PANEL, selectcolor=C_BG,
                   activebackground=C_PANEL).pack(side="left", padx=(14, 2))
    testdir_var = tk.StringVar(value=cfg.test_dir)
    tk.Entry(row2, textvariable=testdir_var, font=mono, fg=C_TEXT, bg=C_BG,
             insertbackground=C_TEXT, relief="flat", width=34).pack(
        side="left", ipady=3)

    def browse_testdir():
        d = filedialog.askdirectory(initialdir=testdir_var.get() or "C:\\tmp")
        if d:
            testdir_var.set(d)
            apply_cfg()
    btn(row2, "…", browse_testdir, C_DIM).pack(side="left", padx=4)

    row3 = tk.Frame(ctrl, bg=C_PANEL); row3.pack(fill="x", pady=(10, 0))
    btn(row3, "▶ Start engine", lambda: (apply_cfg(), in_thread(start_engine, cfg)),
        C_OK).pack(side="left")
    btn(row3, "■ Stop", lambda: in_thread(stop_engine), C_BAD).pack(side="left", padx=6)

    def restart():
        apply_cfg()
        def seq():
            note(stop_engine())
            time.sleep(1.2)
            note(start_engine(cfg))
        threading.Thread(target=seq, daemon=True).start()
    btn(row3, "⟳ Restart", restart, C_WARN).pack(side="left")

    btn(row3, "Ollama ▶", lambda: in_thread(start_ollama), C_OK).pack(side="left", padx=(20, 4))
    btn(row3, "Ollama ■", lambda: in_thread(stop_ollama), C_BAD).pack(side="left")
    btn(row3, "Kill GPU orphans", lambda: in_thread(kill_gpu_orphans), C_WARN
        ).pack(side="left", padx=6)

    btn(row3, "Self-test", lambda: (note("self-test running…"),
                                    in_thread(run_selftest, cfg)),
        C_ACCENT).pack(side="left", padx=(20, 4))
    btn(row3, "Debug view", lambda: webbrowser.open(f"http://127.0.0.1:{DEBUG_PORT}/"),
        C_DIM).pack(side="left", padx=4)
    btn(row3, "Contract", lambda: webbrowser.open(f"http://127.0.0.1:{DEBUG_PORT}/contract"),
        C_DIM).pack(side="left")
    fw_btn = btn(row3, "Fix firewall", lambda: in_thread(fix_firewall), C_DIM)
    fw_btn.pack(side="left", padx=6)

    def apply_cfg():
        cfg.repo_root = repo_var.get().strip()
        cfg.model = model_var.get().strip()
        cfg.use_test_dir = bool(test_var.get())
        cfg.test_dir = testdir_var.get().strip()
        cfg.save()

    # ── notes + log tail ───────────────────────────────────────────────
    note_lbl = lbl(root, "ready.", fg=C_ACCENT, font=ui)
    note_lbl.pack(fill="x", padx=16, pady=(6, 0))

    logf = tk.Frame(root, bg=C_BG)
    logf.pack(fill="both", expand=True, padx=12, pady=(6, 12))
    lbl(logf, f"engine log — {ENGINE_LOG}", fg=C_DIM, font=mono).pack(fill="x")
    text = tk.Text(logf, font=mono, fg=C_TEXT, bg=C_PANEL, relief="flat",
                   height=14, state="disabled", wrap="none")
    text.pack(fill="both", expand=True)

    log_state = {"pos": 0}

    def tail_log():
        try:
            if ENGINE_LOG.exists():
                size = ENGINE_LOG.stat().st_size
                if size < log_state["pos"]:
                    log_state["pos"] = 0          # log rotated/truncated
                if size > log_state["pos"]:
                    with open(ENGINE_LOG, "r", encoding="utf-8",
                              errors="replace") as f:
                        f.seek(log_state["pos"])
                        chunk = f.read()
                        log_state["pos"] = f.tell()
                    text.configure(state="normal")
                    text.insert("end", chunk)
                    # keep the widget bounded
                    if int(text.index("end-1c").split(".")[0]) > 800:
                        text.delete("1.0", "300.0")
                    text.see("end")
                    text.configure(state="disabled")
        except Exception:
            pass
        root.after(1000, tail_log)

    # ── status poll loop (thread) → UI apply loop (after) ─────────────
    def poller():
        while True:
            try:
                status_q.put(snapshot(cfg))
            except Exception as exc:
                status_q.put({"error": str(exc)})
            time.sleep(2.0)

    threading.Thread(target=poller, daemon=True).start()

    def fmt_engine(s) -> tuple[str, str]:
        if not s["engine_up"]:
            hint = "" if s["repo_ok"] else "\n! engine file missing in repo\n  (Browse… to the worktree checkout)"
            return C_BAD, f"stopped{hint}\nbranch {s['branch']}"
        st = s.get("state") or {}
        res = st.get("result") or {}
        plan = (s.get("contract") or {}).get("summary") or {}
        lines = [
            f"UP  :{FRAME_PORT} ws · :{DEBUG_PORT} http  (pid {','.join(map(str, s['engine_pids'])) or '?'})",
            f"frames {st.get('framesIn', 0)} · infers {st.get('inferences', 0)}"
            f" · {st.get('ingestFps', 0)} fps · phones {st.get('clients', 0)}",
            f"sees: {res.get('primary') or '—'} ({res.get('latencyMs', 0)} ms)",
            f"room: {st.get('roomSurfaces', 0)} surfaces · plan v{st.get('planVersion', 0)}",
        ]
        if plan.get("shoppingLine"):
            lines.append(plan["shoppingLine"][:52])
        return C_OK, "\n".join(lines)

    def fmt_ollama(s) -> tuple[str, str]:
        if not s["ollama_up"]:
            return C_BAD, "stopped\n(engine still places rooms without VLM)"
        loaded = s["ollama_loaded"]
        lines = [f"UP  :{OLLAMA_PORT} · models: {len(s['ollama_models'])}"]
        for m in loaded[:2]:
            size = m.get("size") or 1
            vram = m.get("size_vram") or 0
            where = "100% GPU" if vram >= 0.95 * size else \
                    f"{int(100 * vram / size)}% GPU (CPU spill!)"
            lines.append(f"{m.get('name')}: {where}")
        if not loaded:
            lines.append("nothing loaded (loads on first call)")
        return (C_OK if not any("spill" in l for l in lines) else C_WARN,
                "\n".join(lines))

    def fmt_gpu(s) -> tuple[str, str]:
        g = s.get("gpu")
        fw = s.get("firewall", {})
        fw_ok = all(fw.values())
        lines = []
        color = C_OK
        if g:
            pct = int(100 * g["used"] / max(1, g["total"]))
            if pct > 85:
                color = C_WARN
            lines.append(f"VRAM {g['used']}/{g['total']} MiB ({pct}%) · util {g['util']}%")
        else:
            lines.append("nvidia-smi unavailable")
            color = C_DIM
        lines.append(f"LAN {s['lan_ip']}")
        lines.append("firewall 8798/8799: " + ("open" if fw_ok else "MISSING → Fix firewall"))
        if not fw_ok:
            color = C_WARN
        return color, "\n".join(lines)

    def apply_status():
        try:
            while True:
                s = status_q.get_nowait()
                if "error" in s:
                    note_lbl.configure(text=f"status error: {s['error']}", fg=C_BAD)
                    continue
                c, t = fmt_engine(s); eng_dot.configure(fg=c); eng_body.configure(text=t)
                c, t = fmt_ollama(s); oll_dot.configure(fg=c); oll_body.configure(text=t)
                c, t = fmt_gpu(s); gpu_dot.configure(fg=c); gpu_body.configure(text=t)
                phone_var.set(f"ws://{s['lan_ip']}:{FRAME_PORT}/v1/vision")
                branch_lbl.configure(
                    text=f"[{s['branch']}]",
                    fg=C_OK if s["repo_ok"] else C_BAD)
                # refresh model dropdown from installed models
                menu = model_menu["menu"]
                menu.delete(0, "end")
                models = s["ollama_models"] or [model_var.get()]
                for m in models:
                    menu.add_command(label=m,
                                     command=lambda v=m: (model_var.set(v), apply_cfg()))
        except Empty:
            pass
        try:
            while True:
                n = note_q.get_nowait()
                color = C_BAD if str(n).startswith("FAIL") else \
                        C_OK if str(n).startswith("PASS") else C_ACCENT
                note_lbl.configure(text=str(n), fg=color)
        except Empty:
            pass
        root.after(500, apply_status)

    def on_close():
        apply_cfg()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    tail_log()
    apply_status()
    root.mainloop()


# ────────────────────────────────────────────────────────────────────────
# Entry
# ────────────────────────────────────────────────────────────────────────

def main():
    if "--selftest-cli" in sys.argv:
        cfg = Config.load()
        # headless probe of every non-GUI helper — used by CI/agent checks
        s = snapshot(cfg)
        out = {
            "repo_ok": s["repo_ok"], "branch": s["branch"],
            "engine_up": s["engine_up"], "ollama_up": s["ollama_up"],
            "gpu": s["gpu"], "lan_ip": s["lan_ip"], "firewall": s["firewall"],
            "models": s["ollama_models"],
        }
        print(json.dumps(out, indent=2))
        return
    run_gui()


if __name__ == "__main__":
    main()
