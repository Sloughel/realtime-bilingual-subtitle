"""端到端集成测试（任务 1.6）：离线音频 → 真实链路 → 字幕 JSON + 延迟统计。

在**本进程内**启动真实服务端（uvicorn，监听 127.0.0.1 回环端口，无需改防火墙），
再用 :mod:`mock_client` 按协议灌入离线音频，校验字幕 JSON 并产出延迟报告。

默认：加载真实 whisper 模型、按**实时节奏**发送（使端到端延迟可测）。
若 ``server/.env`` 未配置翻译密钥，则翻译自动降级（只出原文），脚本会提示。

用法::

    .venv/Scripts/python.exe scripts/e2e_test.py                 # 默认 1 轮（约 4 句）
    .venv/Scripts/python.exe scripts/e2e_test.py --repeats 3 --fast
    .venv/Scripts/python.exe scripts/e2e_test.py --no-translate   # 跳过 API，仅测识别链路
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = PROJECT_ROOT / "server"
for p in (SERVER_DIR, PROJECT_ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import mock_client  # noqa: E402
from app.main import create_app  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.translate import NullTranslator, TranslationService  # noqa: E402

# 夹具中的真实语音区间（由 RMS 能量轮廓确定，见 docs/04 M1 记录）
SPEECH_REGIONS = [(1.5, 3.9), (5.5, 8.4), (9.4, 12.3), (13.0, 16.5)]
SR = 16000

# 预算（01 文档 NFR-01）
BUDGET_ASR_MS = 800.0
BUDGET_TOTAL_MS = 3000.0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build_audio(repeats: int) -> np.ndarray:
    """把夹具中的语音区间用 0.8s 静音拼接，构造多句测试音频。"""
    pcm = mock_client.load_pcm16(SERVER_DIR / "tests/fixtures/sample_en.wav")
    gap = np.zeros(int(0.8 * SR), dtype=np.int16)
    parts = [gap]
    for _ in range(repeats):
        for a, b in SPEECH_REGIONS:
            parts.append(pcm[int(a * SR) : int(b * SR)])
            parts.append(gap)
    return np.concatenate(parts)


class ServerThread:
    def __init__(self, app, port: int) -> None:
        import uvicorn

        self.config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 120  # 首次加载模型可能较久
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("服务端启动超时")
            time.sleep(0.2)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=15)


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def check_subtitle_shape(s: dict) -> list[str]:
    """校验字幕 JSON 是否满足 03 文档 §5.2 的最小字段定义。"""
    required = [
        "type", "sentence_id", "seq_start", "seq_end", "source", "translation",
        "src_lang", "tgt_lang", "start_ms", "end_ms", "created_ms", "asr_ms", "translate_ms",
    ]
    problems = [f"缺字段 {k}" for k in required if k not in s]
    if s.get("type") != "subtitle":
        problems.append("type 非 subtitle")
    if s.get("seq_end", 0) < s.get("seq_start", 0):
        problems.append("seq_end < seq_start")
    if s.get("end_ms", 0) <= s.get("start_ms", 0):
        problems.append("end_ms <= start_ms")
    if not s.get("source"):
        problems.append("source 为空")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="服务端端到端集成测试（离线音频）")
    ap.add_argument("--repeats", type=int, default=1, help="重复语音区间组数（每轮 4 句）")
    ap.add_argument("--fast", action="store_true", help="不按实时节奏发送")
    ap.add_argument("--no-translate", action="store_true", help="使用降级翻译（不调用 API）")
    ap.add_argument("--report", default="logs/latency_report.json")
    args = ap.parse_args()

    settings = get_settings()
    pcm = build_audio(args.repeats)
    port = free_port()
    url = f"ws://127.0.0.1:{port}/ws?protocol_version=1"

    translation = (
        TranslationService(NullTranslator(), settings) if args.no_translate else None
    )
    app = create_app(settings, translation=translation, load_model=True)

    print("=" * 72)
    print("服务端端到端集成测试（真实 ASR" + ("，真实翻译" if not args.no_translate else "，降级翻译") + "）")
    print("=" * 72)
    print(f"音频     : {pcm.size / SR:.1f}s（{args.repeats} 轮 × 4 句）")
    print(f"回环端口 : {port}    节奏：{'全速' if args.fast else '实时'}")
    if not settings.translate_configured and not args.no_translate:
        print("提示     : server/.env 未配置翻译密钥 → 翻译将降级回传原文")

    with ServerThread(app, port):
        import urllib.request

        health = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/health").read())
        asr_info = health.get("asr", {})
        print(f"识别设备 : {asr_info.get('device')} / {asr_info.get('compute_type')}"
              + ("  （已降级 CPU）" if asr_info.get("degraded") else ""))
        print("-" * 72)

        result = asyncio.run(
            mock_client.stream_audio(url, pcm, realtime=not args.fast)
        )
        status = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/status").read())

    subs = result["subtitles"]
    for s in subs[:8]:
        tag = "  [降级]" if s.get("translation_degraded") else ""
        print(f"#{s['sentence_id']:>2}  {s['source']}")
        print(f"      → {s['translation']}{tag}")
    if len(subs) > 8:
        print(f"… 其余 {len(subs) - 8} 句略")

    # ---- 结构校验 ----
    problems: list[str] = []
    for s in subs:
        problems += [f"#{s.get('sentence_id')}: {p}" for p in check_subtitle_shape(s)]

    # ---- 延迟统计 ----
    server_lat = status.get("latency", {})
    client_lat = (
        [x for x in (mock_client.client_side_latency(s, result["track"]) for s in subs) if x is not None]
        if not args.fast
        else []
    )
    asr_vals = [s.get("asr_ms", 0) for s in subs]

    print("-" * 72)
    print(f"字幕句数 : {len(subs)}（错误 {len(result['errors'])} 条）")
    if asr_vals:
        print(f"ASR      : 平均 {sum(asr_vals)//len(asr_vals)}ms  P95 {pct(asr_vals,0.95):.0f}ms  "
              f"最大 {max(asr_vals)}ms   （NFR-01 预算 {BUDGET_ASR_MS:.0f}ms/句）")
    if server_lat:
        print(f"翻译     : P95 {server_lat.get('translate_ms_p95')}ms   （NFR-01 预算 1500ms/句）")
    if client_lat:
        print(f"端到端   : 平均 {sum(client_lat)//len(client_lat)}ms  "
              f"P95 {pct(client_lat,0.95):.0f}ms  最大 {max(client_lat)}ms  （NFR-01 预算 {BUDGET_TOTAL_MS:.0f}ms）")
    elif args.fast:
        print("端到端   : --fast 模式不测量（音频瞬间发送，时间轴被压缩，结果无意义）")

    # ---- 判定 ----
    verdicts: list[tuple[str, bool, str]] = []
    verdicts.append(("收到字幕", bool(subs), f"{len(subs)} 句"))
    verdicts.append(("字幕 JSON 合规", not problems, "通过" if not problems else f"{len(problems)} 处问题"))
    cpu_mode = "cpu" in str(asr_info.get("device", "")).lower()
    asr_p95 = pct(asr_vals, 0.95)
    verdicts.append(
        (
            f"ASR P95 ≤ {BUDGET_ASR_MS:.0f}ms",
            (asr_p95 <= BUDGET_ASR_MS) or cpu_mode,
            f"{asr_p95:.0f}ms" + ("（CPU 模式，不作硬性判定）" if cpu_mode and asr_p95 > BUDGET_ASR_MS else ""),
        )
    )
    if client_lat and not args.no_translate and settings.translate_configured:
        tot_p95 = pct(client_lat, 0.95)
        verdicts.append((f"端到端 P95 ≤ {BUDGET_TOTAL_MS:.0f}ms", tot_p95 <= BUDGET_TOTAL_MS, f"{tot_p95:.0f}ms"))

    print("-" * 72)
    for name, ok, detail in verdicts:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}：{detail}")
    if problems:
        for p in problems[:10]:
            print(f"       ! {p}")

    report = {
        "settings": {
            "whisper_model": settings.whisper_model,
            "device": asr_info.get("device"),
            "compute_type": asr_info.get("compute_type"),
            "translate_configured": settings.translate_configured,
            "realtime": not args.fast,
            "repeats": args.repeats,
        },
        "server_latency": server_lat,
        "client_latency_ms": client_lat,
        "asr_ms": asr_vals,
        "subtitles": subs,
        "errors": result["errors"],
        "problems": problems,
    }
    out = PROJECT_ROOT / args.report
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已写出：{out}")

    ok = all(v[1] for v in verdicts)
    print("=" * 72)
    print("结论：端到端链路" + ("通过。" if ok else "存在问题，见上。"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
