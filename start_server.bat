@echo off
chcp 65001 >nul
title 实时双语字幕 - 服务端
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [错误] 未找到 .venv 虚拟环境。
  echo        请先按 README「快速开始」安装依赖。
  pause
  exit /b 1
)

echo 正在启动实时双语字幕服务端……
echo.

".venv\Scripts\python.exe" "server\run_server.py"

echo.
echo 服务端已退出。
pause
