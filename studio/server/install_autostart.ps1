# install_autostart.ps1 — make the SPATAIL spine start automatically at login.
# Creates a shortcut to spine_up.vbs in the current user's Startup folder. No admin
# needed (per-user). Re-run to refresh; run uninstall_autostart.ps1 to remove.
#   powershell -ExecutionPolicy Bypass -File studio\server\install_autostart.ps1
$ErrorActionPreference = 'Stop'

$vbs     = Join-Path $PSScriptRoot 'spine_up.vbs'
$startup = [Environment]::GetFolderPath('Startup')
$lnk     = Join-Path $startup 'SPATAIL Spine.lnk'

if (-not (Test-Path $vbs)) { throw "spine_up.vbs not found at $vbs" }

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath   = 'wscript.exe'
$sc.Arguments    = '"' + $vbs + '"'
$sc.WorkingDirectory = $PSScriptRoot
$sc.WindowStyle  = 7        # minimized/hidden
$sc.Description   = 'Start the SPATAIL always-on spine (Blender + job server) at login'
$sc.Save()

Write-Host "[autostart] installed -> $lnk"
Write-Host "[autostart] it runs: wscript $vbs  (silent)"
Write-Host "[autostart] remove with: studio\server\uninstall_autostart.ps1"
