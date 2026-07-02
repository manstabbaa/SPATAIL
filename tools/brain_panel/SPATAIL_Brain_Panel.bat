@echo off
rem SPATAIL Brain Panel launcher — double-click me.
rem Uses pythonw (no console window); falls back to python if missing.
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0spatail_brain_panel.py"
) else (
  start "" python "%~dp0spatail_brain_panel.py"
)
