# start_spatail_servers.ps1 — bring up the full SPATAIL server stack (idempotent).
#
#   BRAIN   Ollama VLM (:11434) + the fusion vision engine (frame WS :8798, debug :8799)
#           — started via the Brain Panel's own code path so the panel re-attaches cleanly
#           (merged log at %APPDATA%\SPATAIL\logs\vision_engine.log, single-slot Ollama env).
#   SPINE   Blender bridge (:9876) — the MCP add-on binds this on launch.
#   WEB     the .claude/launch.json dev/preview servers (webxr, detector, job, viewers, devlog).
#
# Skips anything already listening. Best-effort: a server that fails to bind is reported,
# never fatal — the brain is what matters. Logs land under %APPDATA%\SPATAIL\logs.
#
#   powershell -ExecutionPolicy Bypass -File C:\SPATAIL_MAX\tools\start_spatail_servers.ps1
$ErrorActionPreference = 'SilentlyContinue'

$Root    = 'C:\SPATAIL_MAX'
$Py      = if (Test-Path 'C:\Users\manst\AppData\Local\Programs\Python\Python311\python.exe') {
             'C:\Users\manst\AppData\Local\Programs\Python\Python311\python.exe'
           } else { (Get-Command python -ErrorAction SilentlyContinue).Source }
$Node    = (Get-Command node -ErrorAction SilentlyContinue).Source
$Blender = if ($env:BLENDER_EXE) { $env:BLENDER_EXE } else { 'C:\Program Files\Blender Foundation\Blender 5.1\blender.exe' }
$LogDir  = Join-Path $env:APPDATA 'SPATAIL\logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Test-Port($p) { [bool](Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue) }

# --- web/http server launcher ------------------------------------------------
function Start-Web {
  param([string]$Name, [int]$Port, [string]$Exe, [string[]]$ArgList, [string]$Check)
  if (Test-Port $Port)                     { Write-Host ("[skip] {0,-26} :{1} already up"     -f $Name,$Port); return }
  if (-not $Exe -or -not (Test-Path $Exe)) { Write-Host ("[MISS] {0,-26} runtime not found"  -f $Name);       return }
  if ($Check -and -not (Test-Path (Join-Path $Root $Check))) {
                                             Write-Host ("[MISS] {0,-26} script missing: {1}" -f $Name,$Check); return }
  $log = Join-Path $LogDir ("{0}.out.log" -f $Name)
  $err = Join-Path $LogDir ("{0}.err.log" -f $Name)
  Start-Process -FilePath $Exe -ArgumentList $ArgList -WorkingDirectory $Root -WindowStyle Hidden `
                -RedirectStandardOutput $log -RedirectStandardError $err
  Write-Host ("[up]   {0,-26} :{1}" -f $Name,$Port)
}

Write-Host "=== SPATAIL server stack ===`n"

# --- 1. BRAIN: Ollama + vision engine, via the Brain Panel's tested code ------
if ((Test-Port 11434) -and (Test-Port 8799)) {
  Write-Host "[skip] brain (ollama + engine)  already up"
} else {
  $brain = @'
import sys, time
sys.path.insert(0, r'C:\SPATAIL_MAX\tools\brain_panel')
import spatail_brain_panel as bp
cfg = bp.Config.load()
cfg.repo_root   = r'C:\SPATAIL_MAX'   # pin the panel to the canonical checkout
cfg.model       = 'qwen2.5vl:3b'      # 3b runs 100% on the 8GB GPU (~0.6s); 7b is CPU-bound
cfg.vlm_timeout = 8                   # LIVE_BRAIN_SPEC 1.4: VLM timeout is 8 s (matches the panel default since b3a9329)
cfg.extra_args  = '--vlm-url http://127.0.0.1:8098/v1'  # llama-server with GPU mmproj (see 1b); drop to use ollama :11434
cfg.use_test_dir = False
cfg.test_dir    = ''
cfg.save()
print('  ollama:', bp.start_ollama())
time.sleep(1.2)
print('  engine:', bp.start_engine(cfg))
'@
  Write-Host "[..]   brain (ollama + engine)"
  $brain | & $Py -
}

# --- 1b. VLM serving: ollama's bundled llama-server with the projector ON GPU
#         ollama 0.30.10 CPU-pins the qwen2.5vl vision tower on this 8GB card
#         ("disabling multimodal projector offload" reason=limited-vram — no env
#         override exists: ollama/ollama#10889, #13742) -> ~9s per NEW image,
#         which the 8s VLM cap turns into 100% identification timeouts on live
#         phone frames. The SAME 3b blob under llama-server with CUDA + GPU
#         mmproj: 0.3-1.1s per fresh image. The engine points at it via
#         --vlm-url http://127.0.0.1:8098/v1 (panel extra_args, pinned below).
$Llama = 'C:\Users\manst\AppData\Local\Programs\Ollama\lib\ollama\llama-server.exe'
$Blob  = 'C:\Users\manst\.ollama\models\blobs\sha256-e9758e589d443f653821b7be9bb9092c1bf7434522b70ec6e83591b1320fdb4d'  # qwen2.5vl:3b (re-resolve after `ollama pull`: ollama show qwen2.5vl:3b --modelfile)
if (Test-Port 8098) {
  Write-Host "[skip] vlm (llama-server)       :8098 already up"
} elseif ((Test-Path $Llama) -and (Test-Path $Blob)) {
  # The bare exe is CPU-only: ggml loads its CUDA backend ONLY via
  # GGML_BACKEND_PATH pointing at the ggml-cuda.dll FILE (not the dir).
  # Use cuda_v12 (self-contained: ships cudart/cublas); cuda_v13 lacks cudart.
  # Verified on the RTX 5060 Ti: CPU-only = ~9-14s per fresh image;
  # CUDA = 0.3-1.1s (the spec's ~1 Hz VLM clock).
  $llamaDir = Split-Path $Llama
  $env:GGML_BACKEND_PATH = Join-Path $llamaDir 'cuda_v12\ggml-cuda.dll'
  $env:PATH = (Join-Path $llamaDir 'cuda_v12') + ';' + $llamaDir + ';' + $env:PATH
  $log = Join-Path $LogDir 'spatail-vlm.out.log'
  $err = Join-Path $LogDir 'spatail-vlm.err.log'
  Start-Process -FilePath $Llama -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $err -ArgumentList @(
    '--model', $Blob, '--mmproj', $Blob, '--port', '8098', '--host', '127.0.0.1',
    '-c', '2048', '-np', '1', '--no-webui', '-ngl', '99',
    '--chat-template', 'chatml', '--no-jinja', '--flash-attn', 'auto', '-b', '512', '-ub', '512', '--no-mmap')
  Write-Host "[up]   vlm (llama-server)       :8098 (CUDA, GPU mmproj)"
} else {
  Write-Host "[MISS] vlm (llama-server)       binary or 3b blob not found - engine will fall back to ollama"
}

# --- 1c. warm the VLM so the first live identification never pays the lazy load
if (-not (Test-Port 8098)) { Start-Sleep -Seconds 3 }
try {
  $warm = @{ model = 'qwen2.5vl:3b'; messages = @(@{ role = 'user'; content = 'hi' }); max_tokens = 2 } | ConvertTo-Json -Depth 4 -Compress
  Invoke-RestMethod -Uri 'http://127.0.0.1:8098/v1/chat/completions' -Method Post -ContentType 'application/json' -Body $warm -TimeoutSec 180 | Out-Null
  Write-Host "[up]   VLM warmed                qwen2.5vl:3b resident on :8098"
} catch { Write-Host ('[warn] VLM warm-up failed: ' + $_.Exception.Message) }

# --- 2. SPINE: Blender bridge (:9876) ----------------------------------------
if (Test-Port 9876) {
  Write-Host "[skip] blender bridge           :9876 already up"
} elseif (Test-Path $Blender) {
  Start-Process -FilePath $Blender -WindowStyle Minimized
  Write-Host "[up]   blender bridge           :9876 (add-on binding...)"
} else {
  Write-Host "[MISS] blender                  not found (set BLENDER_EXE)"
}

# --- 3. WEB: the launch.json dev/preview servers -----------------------------
Start-Web 'spatail-webxr'   8765 $Py   @('-m','http.server','8765')                                'webxr'
Start-Web 'spatail-detector' 8766 $Py  @('webxr/live/detector_server.py','--port','8766')          'webxr/live/detector_server.py'
Start-Web 'spatail-jobserver' 8788 $Py @('studio/server/job_server.py','--port','8788','--no-watchdog','--no-keep-awake') 'studio/server/job_server.py'
Start-Web 'studio-viewer'   5180 $Py   @('studio/viewer/server.py')                                'studio/viewer/server.py'
Start-Web 'devlog'          4137 $Py   @('-m','http.server','4137','--directory','mydevelopertools') 'mydevelopertools'

# --- 4. settle + status table ------------------------------------------------
Start-Sleep -Seconds 3
Write-Host "`n=== status ==="
$svc = [ordered]@{
  'ollama (pulls)'      = 11434
  'vlm (llama-server)'  = 8098
  'vision engine ws'    = 8798
  'vision engine debug' = 8799
  'blender bridge'      = 9876
  'webxr'               = 8765
  'detector'            = 8766
  'job server'          = 8788
  'studio-viewer'       = 5180
  'devlog'              = 4137
}
foreach ($k in $svc.Keys) {
  $up = Test-Port $svc[$k]
  "{0,-22} :{1,-6} {2}" -f $k, $svc[$k], $(if ($up) { 'UP' } else { 'down' })
}
$ip = (Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp -ErrorAction SilentlyContinue |
       Where-Object { $_.IPAddress -notlike '169.*' } | Select-Object -First 1).IPAddress
if (-not $ip) { $ip = '192.168.1.160' }
Write-Host ("`nphone -> ws://{0}:8798/v1/vision   debug -> http://127.0.0.1:8799/" -f $ip)
Write-Host "logs  -> $LogDir"
