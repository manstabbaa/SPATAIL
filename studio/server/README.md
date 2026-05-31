# SPATAIL generative AR job server (PC side)

Implements [`docs/generative_ar_contract.md`](../../docs/generative_ar_contract.md):
**iPhone prompt -> this server -> live Blender (MCP) -> USDZ -> iPhone downloads -> AR.**

```
POST /jobs            {prompt, client}   -> {id, status}
GET  /jobs/{id}                           -> {id, status, stage, message, usdz_url?, metadata_url?}
GET  /artifacts/{file}                    -> USDZ / metadata bytes
GET  /health                              -> liveness + Blender bridge status
```

## How it works

- A single background worker serialises jobs (Blender is single-threaded).
- Each job drives the **LIVE** first-party Blender MCP bridge (`localhost:9876`,
  the "MCP" add-on, auto-start) - **no headless Blender is spawned**; the user's
  open Blender does the work and stays open.
- **Real generative authoring** (`generator.py` + `llm_author.py`): for each job
  the scene is **cleared** to a single `gen_root` empty, then **Claude authors a
  Blender-Python script** (via the local `claude` CLI in headless `-p` mode, using
  your existing Claude Code login — no API key) that *models a recognizable
  representation of the subject and animates the described action* as a seamless
  baked loop, parented to `gen_root`. The script runs in the live Blender with
  **self-repair** (a Blender traceback is fed back for a corrected script, up to 3
  tries). "an apple falling from a tree" → a modelled tree + apple that falls, not
  a grey sphere. There is **no primitive fallback** — if authoring fails the job
  errors rather than shipping a box.
- The result is exported to USDZ using the **exact** settings from
  `studio/blender/build_studio.py::_export_usdz` (Y-up, `meters_per_unit=1`,
  `generate_preview_surface`, baked animation), measured/scaled over all of
  `gen_root`'s descendants to a ≤0.9 m tabletop footprint, so it loads in AR Quick
  Look / RealityKit.
- Artifacts land in `studio/out/gen/<job>.usdz` (+ `<job>_metadata.json`).

## Run

```powershell
python studio\server\job_server.py            # binds 0.0.0.0:8787
```

Prereqs: **Blender open** with the MCP add-on server running (it auto-starts ~1 s
after launch). `python` = system Python 3.11 (stdlib only; no installs).

## Reachability (Tailscale)

Bind is `0.0.0.0`, so the server answers on the Tailscale adapter the moment
Tailscale is up. The iOS app stores one **base URL** in Settings:

```
http://<pc-magicdns-name>.<tailnet>.ts.net:8787      # e.g. mansourspc.<tailnet>.ts.net
http://100.x.y.z:8787                                  # raw tailnet IP fallback
```

No firewall edits: Windows Firewall still blocks LAN inbound by default; Tailscale
permits its own traffic.

## Quick test (PowerShell)

```powershell
$b = "http://127.0.0.1:8787"
$id = (Invoke-RestMethod -Method Post -Uri "$b/jobs" -ContentType application/json `
        -Body (@{prompt="a bouncing red ball"; client="test"} | ConvertTo-Json)).id
do { Start-Sleep 2; $s = Invoke-RestMethod "$b/jobs/$id"; $s.stage } while ($s.status -in 'queued','running')
Invoke-WebRequest "$b$($s.usdz_url)" -OutFile "$env:TEMP\$id.usdz"
```

## Prompts (v0.2 - LLM-authored)

Prompts are **open-ended natural language** - Claude authors the geometry +
animation, so there is no fixed shape/colour/motion vocabulary. Describe an object
and an action: "an apple falling from a tree", "a red rubber ball bouncing",
"a spinning globe on a stand", "a rocket lifting off".

Authoring backend:
- Uses the local **`claude` CLI** in headless mode with your existing Claude Code
  login - **no API key needed**. The CLI path auto-resolves to the newest installed
  version; override with `SPATAIL_CLAUDE_CLI`.
- Override the model with `SPATAIL_GEN_MODEL` (else the CLI default).
- If the `claude` CLI is missing, jobs error - there is **no primitive fallback**
  (we never silently ship a grey box).

Each job first **wipes the Blender scene** to a clean `gen_root`, so nothing from a
previous prompt leaks through.
