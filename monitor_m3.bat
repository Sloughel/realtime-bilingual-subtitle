@echo off
title M3 Monitor - Realtime Bilingual Subtitle
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto NO_VENV

echo Starting M3 monitor (45 minutes, 10s interval)...
echo Keep this window open during the test.
echo.
".venv\Scripts\python.exe" "scripts\monitor_server.py" --duration-min 45 --interval-s 10
echo.
pause
exit /b 0

:NO_VENV
echo [ERROR] .venv not found.
pause
exit /b 1
