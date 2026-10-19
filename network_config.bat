@echo off
title Network Config - Realtime Bilingual Subtitle
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator privileges...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

if not exist ".venv\Scripts\python.exe" goto NO_VENV

".venv\Scripts\python.exe" "server\netconfig.py"
echo.
pause
exit /b 0

:NO_VENV
echo [ERROR] .venv not found.
pause
exit /b 1
