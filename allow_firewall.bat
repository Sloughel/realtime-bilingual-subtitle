@echo off
title Allow TCP 8000 for ZeroTier
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 goto NO_ADMIN

if not exist ".venv\Scripts\python.exe" goto NO_VENV

echo Applying firewall rule for ZeroTier subnet (10.243.9.0/24)...
echo.
".venv\Scripts\python.exe" "server\netconfig.py" --firewall zerotier
echo.
echo Current rule:
netsh advfirewall firewall show rule name="Realtime Bilingual Subtitle"
echo.
pause
exit /b 0

:NO_ADMIN
echo.
echo [ERROR] Administrator privileges are required.
echo Right-click this file and choose "Run as administrator",
echo or use network_config.bat which requests elevation automatically.
echo.
pause
exit /b 1

:NO_VENV
echo [ERROR] .venv not found.
pause
exit /b 1
