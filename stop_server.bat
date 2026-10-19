@echo off
title Stop Realtime Bilingual Subtitle Server
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto NO_VENV

echo Looking for the server process...
echo.
".venv\Scripts\python.exe" "server\stop_server.py"
echo.
pause
exit /b 0

:NO_VENV
echo [ERROR] .venv not found.
pause
exit /b 1
