# spine_up.ps1 — bring up the SPATAIL "always-on spine":
#   1. Blender (so its MCP add-on auto-starts the socket on :9876), if not running
#   2. the job server on 0.0.0.0:8787 (keep-awake + Blender watchdog built in)
#
# Idempotent: skips whatever is already up. Safe to run at login or by hand.
#   powershell -ExecutionPolicy Bypass -File studio\server\spine_up.ps1
$ErrorActionPreference = 'SilentlyContinue'

$root      = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent   # C:\SPATAIL_MAX
$blender   = if ($env:BLENDER_EXE) { $env:BLENDER_EXE } else { 'C:\Program Files\Blender Foundation\Blender 5.1\blender.exe' }
$python    = if ($env:SPATAIL_PYTHON) { $env:SPATAIL_PYTHON } else { 'C:\Users\manst\AppData\Local\Programs\Python\Python311\python.exe' }
$server    = Join-Path $PSScriptRoot 'job_server.py'

function Test-Port($p) { [bool](Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue) }

# --- 1. Blender (bridge on 9876) ---
if (Test-Port 9876) {
    Write-Host "[spine] Blender bridge already up (9876)"
} elseif (Test-Path $blender) {
    Write-Host "[spine] launching Blender -> $blender"
    Start-Process -FilePath $blender
    # give the add-on ~a few seconds to bind the socket
    for ($i = 0; $i -lt 20 -and -not (Test-Port 9876); $i++) { Start-Sleep -Milliseconds 700 }
    Write-Host ("[spine] bridge {0}" -f $(if (Test-Port 9876) { 'up' } else { 'not up yet (give it a moment)' }))
} else {
    Write-Host "[spine] WARNING: Blender not found at $blender (set BLENDER_EXE)"
}

# --- 2. job server (8787) ---
if (Test-Port 8787) {
    Write-Host "[spine] job server already up (8787)"
} elseif (Test-Path $python) {
    Write-Host "[spine] starting job server -> $python $server"
    Start-Process -FilePath $python -ArgumentList @($server, '--host', '0.0.0.0', '--port', '8787') -WorkingDirectory $root
    for ($i = 0; $i -lt 20 -and -not (Test-Port 8787); $i++) { Start-Sleep -Milliseconds 500 }
    Write-Host ("[spine] job server {0}" -f $(if (Test-Port 8787) { 'up' } else { 'not up yet' }))
} else {
    Write-Host "[spine] ERROR: python not found at $python (set SPATAIL_PYTHON)"
}

Write-Host ("[spine] status: blender(9876)={0}  server(8787)={1}" -f (Test-Port 9876), (Test-Port 8787))
