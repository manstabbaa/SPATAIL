# uninstall_autostart.ps1 — remove the SPATAIL spine login auto-start shortcut.
#   powershell -ExecutionPolicy Bypass -File studio\server\uninstall_autostart.ps1
$startup = [Environment]::GetFolderPath('Startup')
$lnk     = Join-Path $startup 'SPATAIL Spine.lnk'
if (Test-Path $lnk) {
    Remove-Item $lnk -Force
    Write-Host "[autostart] removed $lnk"
} else {
    Write-Host "[autostart] nothing to remove (no shortcut at $lnk)"
}
