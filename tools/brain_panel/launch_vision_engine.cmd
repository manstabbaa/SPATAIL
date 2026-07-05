@echo off
rem SPATAIL live vision engine launcher (same flags the brain panel uses).
rem Lives in the repo because AppData\Roaming\SPATAIL is not accessible to
rem service-spawned (WMI/scheduler) processes on this PC (found 2026-07-05).
rem Logs to studio\out\logs\vision_engine_live.log. Safe to double-click
rem if 8798/8799 are free.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d C:\SPATAIL_MAX
if not exist studio\out\logs mkdir studio\out\logs
"C:\Users\manst\AppData\Local\Programs\Python\Python311\python.exe" -u pipeline\server\spatail_vision_engine.py --vlm-model qwen2.5vl:3b --vlm-timeout 8 --vlm-url http://127.0.0.1:8098/v1 >> studio\out\logs\vision_engine_live.log 2>&1
