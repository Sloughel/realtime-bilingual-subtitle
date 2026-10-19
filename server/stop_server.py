"""停止占用服务端端口的进程（仅限 Python，避免误杀其他程序）。

用法::

    .venv/Scripts/python.exe server/stop_server.py
    .venv/Scripts/python.exe server/stop_server.py --port 8000 --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from netutil import port_owner  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="停止服务端（按端口查找进程）")
    ap.add_argument("--port", type=int, default=None, help="默认取 server/.env 的 SERVER_PORT")
    ap.add_argument("--yes", action="store_true", help="不询问，直接结束")
    args = ap.parse_args()

    port = args.port
    if port is None:
        from app.settings import get_settings

        port = get_settings().server_port

    owner = port_owner(port)
    if owner is None:
        print(f"端口 {port} 没有被监听，服务端可能已经停止。")
        return 0

    pid, name, exe = owner
    print(f"端口 {port} 被 PID {pid}（{name}）占用：{exe}")
    if name.lower() not in ("python.exe", "pythonw.exe", "python"):
        print("该进程不是 Python，已拒绝终止，请手动确认。")
        return 1

    if not args.yes:
        answer = input("确认结束该进程？[y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("已取消。")
            return 1

    try:
        import psutil

        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except Exception as exc:  # noqa: BLE001
        print(f"终止失败：{exc}")
        return 1

    print(f"已停止 PID {pid}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
