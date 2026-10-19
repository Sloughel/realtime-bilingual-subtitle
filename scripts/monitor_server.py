"""M3 稳定性监视：定期采样服务端进程 RSS 与 GPU 显存，输出 CSV 与汇总。

在测试开始前双击 `monitor_m3.bat` 启动，然后按 M3 操作单执行测试；
结束后回来看终端汇总，或把 `logs/m3_monitor.csv` 交给 agent 分析。

用法::

    .venv/Scripts/python.exe scripts/monitor_server.py
    .venv/Scripts/python.exe scripts/monitor_server.py --duration-min 45 --interval-s 10
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import time
from datetime import datetime
from pathlib import Path

import psutil


def server_pid(port: int) -> int | None:
    try:
        for conn in psutil.net_connections(kind="inet"):
            if (
                conn.status == psutil.CONN_LISTEN
                and conn.laddr is not None
                and conn.laddr.port == port
                and conn.pid
            ):
                return conn.pid
    except Exception:
        pass
    return None


def gpu_used_mb() -> int | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        return int(out.strip().splitlines()[0])
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="M3 稳定性监视")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--duration-min", type=float, default=45)
    ap.add_argument("--interval-s", type=float, default=10)
    ap.add_argument("--out", default="logs/m3_monitor.csv")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rss_values: list[float] = []
    gpu_values: list[int] = []
    samples = 0
    end = time.time() + args.duration_min * 60

    print("=" * 68)
    print("M3 稳定性监视")
    print("=" * 68)
    print(f"端口 {args.port}，采样间隔 {args.interval_s}s，时长 {args.duration_min} 分钟")
    print(f"输出 {out}（Ctrl+C 可提前结束）")
    print("=" * 68)

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "pid", "rss_mb", "gpu_used_mb"])
        try:
            while time.time() < end:
                pid = server_pid(args.port)
                rss = None
                if pid:
                    try:
                        rss = round(psutil.Process(pid).memory_info().rss / 1024 / 1024, 1)
                    except Exception:
                        pass
                gpu = gpu_used_mb()
                writer.writerow(
                    [datetime.now().strftime("%H:%M:%S"), pid or "", rss or "", gpu if gpu is not None else ""]
                )
                f.flush()
                if rss is not None:
                    rss_values.append(rss)
                if gpu is not None:
                    gpu_values.append(gpu)
                samples += 1
                time.sleep(args.interval_s)
        except KeyboardInterrupt:
            print("\n已手动结束采样。")

    print("-" * 68)
    print(f"采样点数：{samples}")
    if rss_values:
        growth = rss_values[-1] - rss_values[0]
        print(
            f"服务端 RSS：min {min(rss_values):.1f} MB  max {max(rss_values):.1f} MB  "
            f"首末变化 {growth:+.1f} MB"
        )
    else:
        print("未采集到服务端进程（可能测试期间服务端未启动）")
    if gpu_values:
        print(f"GPU 显存（整卡）：min {min(gpu_values)} MB  max {max(gpu_values)} MB")
    print(f"明细文件：{out}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
