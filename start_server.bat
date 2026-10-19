@echo off
title Realtime Bilingual Subtitle - Server
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto NO_VENV

echo Starting Realtime Bilingual Subtitle server...
echo Press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" "server\run_server.py"
set CODE=%ERRORLEVEL%
echo.
echo Server exited. Code: %CODE%
pause
exit /b %CODE%

:NO_VENV
echo.
echo [ERROR] .venv not found.
echo Please install dependencies first (see README "Quick Start").
pause
exit /b 1
