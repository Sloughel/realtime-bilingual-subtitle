#!/usr/bin/env bash
# 停止占用服务端端口的进程（Linux / macOS，E3）
#
# 用法：
#   ./stop_server.sh                 # 按 server/.env 的 SERVER_PORT 查找
#   ./stop_server.sh --port 8001
#   ./stop_server.sh --yes           # 不询问
#
# 只结束监听该端口的进程（默认还会再做一次 Python 进程校验），
# 不会按名字批量杀进程。实际逻辑复用 server/stop_server.py，与 Windows 端一致。
set -euo pipefail

cd "$(dirname "$0")"

if [[ -n "${PYTHON:-}" ]]; then
  PY="$PYTHON"
elif [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
elif [[ -x ".venv-linux/bin/python" ]]; then
  PY=".venv-linux/bin/python"
else
  PY="python3"
fi

exec "$PY" server/stop_server.py "$@"
