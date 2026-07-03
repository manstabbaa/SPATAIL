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
cfg.vlm_timeout = 8                   # LIVE_BRAIN_SPEC 1.4: VLM timeout default is 8 s (panel dataclass still says 90)
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
  'ollama (VLM)'        = 11434
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
