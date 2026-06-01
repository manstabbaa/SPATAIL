' spine_up.vbs — launch the SPATAIL spine silently (no console window).
' Used by the Startup-folder shortcut so Blender + the job server come back
' automatically after login. Calls spine_up.ps1 hidden.
Dim shell, here, ps1
Set shell = CreateObject("WScript.Shell")
here = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
ps1 = here & "\spine_up.ps1"
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1 & """", 0, False
