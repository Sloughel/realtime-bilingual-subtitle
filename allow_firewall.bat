@echo off
title Allow TCP 8000 for ZeroTier
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 goto NO_ADMIN

echo Adding inbound rule: TCP 8000, allowed only from ZeroTier subnet 10.243.9.0/24
echo.
netsh advfirewall firewall add rule name="Realtime Bilingual Subtitle (ZeroTier)" dir=in action=allow protocol=TCP localport=8000 remoteip=10.243.9.0/24 profile=any
echo.
echo Current rule:
netsh advfirewall firewall show rule name="Realtime Bilingual Subtitle (ZeroTier)"
echo.
echo Done. You can close this window.
pause
exit /b 0

:NO_ADMIN
echo.
echo [ERROR] Administrator privileges are required.
echo Right-click this file and choose "Run as administrator".
echo.
pause
exit /b 1
