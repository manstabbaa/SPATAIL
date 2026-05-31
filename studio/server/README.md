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
- `generator.py` parses the prompt (shape, colour, motion, size), builds the
  geometry, bakes a **seamless looping** animation, and exports USDZ using the
  **exact** settings from `studio/blender/build_studio.py::_export_usdz`
  (Y-up, `meters_per_unit=1`, `generate_preview_surface`, baked animation) so it
  loads in AR Quick Look / RealityKit.
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

## Prompt vocabulary (v0.1)

- **shapes**: ball/sphere, cube/box, cylinder/can, cone, pyramid, torus/donut/ring
- **colours**: red, orange, yellow, gold, green, blue, purple, pink, cyan, white,
  black, grey, silver, brown, ...
- **motion**: bounce, spin, roll, orbit, hover/float, pulse, wobble (default: spin)
- **size**: tiny, small, (default), big/large, huge/giant

Unknown prompt -> sensible default (a spinning object). The `parse_prompt` front end
is intentionally swappable for an LLM-backed generator later without touching the
server or the export path.
