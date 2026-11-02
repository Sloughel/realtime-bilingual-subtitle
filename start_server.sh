#!/usr/bin/env bash
# 服务端启动器（Linux / macOS，E3）
#
# 用法：
#   ./start_server.sh              # 用仓库虚拟环境启动
#   PYTHON=python3 ./start_server.sh
#
# 与 start_server.bat 行为一致：定位虚拟环境 → 交给 server/run_server.py
# （后者负责端口占用预检、打印手机端应填 IP、按配置决定 ws/wss）。
set -euo pipefail

cd "$(dirname "$0")"

# ---- 1. 定位解释器：优先显式 PYTHON，其次是仓库内虚拟环境 ----
if [[ -n "${PYTHON:-}" ]]; then
  PY="$PYTHON"
elif [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
elif [[ -x ".venv-linux/bin/python" ]]; then
  PY=".venv-linux/bin/python"
else
  cat <<'EOF'

[错误] 没有找到虚拟环境 .venv/bin/python。

首次使用请先安装依赖（约 5~10 分钟，含 faster-whisper 与 onnxruntime）：

    python3 -m venv .venv
    .venv/bin/python -m pip install -r server/requirements.txt

GPU 机器还需安装 CUDA 12 + cuDNN 9 的运行时（requirements 已包含
nvidia-cublas-cu12 / nvidia-cudnn-cu12，无需额外操作）。
CPU 机器请在 server/.env 设 WHISPER_DEVICE=cpu（延迟会明显变高）。

EOF
  exit 1
fi

# ---- 2. 依赖自检：缺包时给出可复制的修复命令，而不是 ImportError 栈 ----
if ! "$PY" - <<'PYCHECK' >/dev/null 2>&1
import importlib.util, sys
missing = [m for m in ("fastapi", "uvicorn", "faster_whisper", "onnxruntime", "numpy")
           if importlib.util.find_spec(m) is None]
sys.exit(1 if missing else 0)
PYCHECK
then
  echo "[错误] 虚拟环境里缺少服务端依赖。请执行：" >&2
  echo "       $PY -m pip install -r server/requirements.txt" >&2
  exit 1
fi

# ---- 3. 配置存在性提示（不读取、不打印任何密钥）----
if [[ ! -f "server/.env" ]]; then
  echo "[提示] 未发现 server/.env，将全部使用默认配置。"
  echo "       需要翻译能力时：cp server/.env.example server/.env 后填写。"
  echo
fi

echo "启动实时双语字幕服务端，按 Ctrl+C 停止…"
exec "$PY" server/run_server.py
