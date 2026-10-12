"""模拟客户端：把离线 wav 按 03 文档协议灌入**正在运行**的服务端并收集字幕。

用途：不依赖 Android 客户端即可验证服务端全链路（04 文档 §4「集成测试」）。

用法::

    # 终端 A：启动服务端
    cd server && ../.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
    # 终端 B：灌入离线音频
    .venv/Scripts/python.exe scripts/mock_client.py --wav server/tests/fixtures/sample_en.wav --realtime
    .venv/Scripts/python.exe scripts/mock_client.py --wav some.wav --fast --dump logs/last_run.json

也可作为库使用：``from mock_client import stream_audio``（见 scripts/e2e_test.py）。
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = PROJECT_ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from app.protocol import CHUNK_MS, CHUNK_SAMPLES, encode_audio_frame  # noqa: E402


def now_ms() -> int:
    return int(time.time() * 1000)


def load_pcm16(path: str | Path) -> np.ndarray:
    """把任意音频解码为 16kHz 单声道 int16（与客户端上传格式一致，ADR-06）。"""
    from faster_whisper.audio import decode_audio

    audio = decode_audio(str(path), sampling_rate=16000).astype(np.float32)
    return np.clip(audio * 32767, -32768, 32767).astype(np.int16)


def make_frames(pcm16: np.ndarray):
    """按 100ms 切帧并编号（从 1 起）。"""
    frames = []
    seq = 0
    for i in range(0, pcm16.size, CHUNK_SAMPLES):
        chunk = pcm16[i : i + CHUNK_SAMPLES]
        if chunk.size < CHUNK_SAMPLES:
            chunk = np.concatenate(
                [chunk, np.zeros(CHUNK_SAMPLES - chunk.size, dtype=np.int16)]
            )
        seq += 1
        frames.append((seq, encode_audio_frame(seq, chunk)))
    return frames


def client_side_latency(subtitle: dict, track: list[tuple[int, int]]) -> int | None:
    """以客户端视角计算「句末 → 字幕上屏」延迟。

    ``track`` 为 ``(音频结束毫秒, 发送墙钟毫秒)`` 的有序列表。定位该句 ``end_ms``
    对应的发送时刻，用字幕 ``created_ms`` 相减（NFR-01 判据）。
    """
    if not track:
        return None
    marks = [t[0] for t in track]
    idx = max(0, min(bisect.bisect_left(marks, subtitle.get("end_ms", 0)), len(track) - 1))
    sent_ms = track[idx][1]
    created = subtitle.get("created_ms")
    if created is None:
        return None
    return max(0, created - sent_ms)


async def stream_audio(
    url: str,
    pcm16: np.ndarray,
    *,
    realtime: bool = True,
    src: str = "en",
    tgt: str = "zh",
    mode: str = "mic",
    idle_timeout_s: float = 6.0,
    on_subtitle=None,
) -> dict:
    """连接服务端并灌入音频，返回 ``{subtitles, errors, status, track, elapsed_s}``。"""
    import websockets

    subtitles: list[dict] = []
    errors: list[dict] = []
    notices: list[dict] = []
    track: list[tuple[int, int]] = []
    frames = make_frames(pcm16)

    t0 = time.perf_counter()
    async with websockets.connect(url, max_size=None) as ws:
        hello = json.loads(await ws.recv())
        await ws.send(json.dumps({"type": "config", "mode": mode, "src_lang": src, "tgt_lang": tgt}))
        ack = json.loads(await ws.recv())
        await ws.send(json.dumps({"type": "start"}))

        for seq, payload in frames:
            await ws.send(payload)
            track.append((seq * CHUNK_MS, now_ms()))
            if realtime:
                await asyncio.sleep(CHUNK_MS / 1000)
        await ws.send(json.dumps({"type": "stop"}))

        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=idle_timeout_s)
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == "subtitle":
                subtitles.append(msg)
                if on_subtitle is not None:
                    on_subtitle(msg)
            elif kind == "error":
                errors.append(msg)
            elif kind == "status":
                notices.append(msg)

    return {
        "hello": hello,
        "config_ack": ack,
        "subtitles": subtitles,
        "errors": errors,
        "notices": notices,
        "track": track,
        "elapsed_s": round(time.perf_counter() - t0, 2),
    }


async def _main_async(args) -> int:
    pcm = load_pcm16(args.wav)
    if args.limit_s:
        pcm = pcm[: int(args.limit_s * 16000)]
    url = f"ws://{args.host}:{args.port}/ws?protocol_version=1"

    print("=" * 72)
    print("模拟客户端 → 服务端")
    print("=" * 72)
    print(f"服务端   : {url}")
    print(f"音频     : {args.wav}（{pcm.size / 16000:.1f}s）")
    print(f"节奏     : {'实时' if not args.fast else '全速'}   语言：{args.src} → {args.tgt}")
    print("-" * 72)

    def show(msg: dict) -> None:
        print(f"[字幕 #{msg['sentence_id']}] {msg['source']}")
        print(f"           → {msg['translation']}"
              + ("   （翻译失败，已回传原文）" if msg.get("translation_degraded") else ""))

    result = await stream_audio(
        url, pcm, realtime=not args.fast, src=args.src, tgt=args.tgt, on_subtitle=show
    )

    subs = result["subtitles"]
    print("-" * 72)
    print(f"收到字幕 {len(subs)} 句，错误 {len(result['errors'])} 条，耗时 {result['elapsed_s']}s")
    lat = [client_side_latency(s, result["track"]) for s in subs]
    lat = [x for x in lat if x is not None]
    if lat:
        print(f"端到端延迟（句末→上屏）：平均 {sum(lat)//len(lat)}ms，最大 {max(lat)}ms")
    for e in result["errors"][:10]:
        print(f"  [error {e.get('code')}] {e.get('message')}")

    if args.dump:
        out = Path(args.dump)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(result)
        payload["client_latency_ms"] = lat
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写出：{out}")
    return 0 if subs else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="模拟客户端：向服务端灌入离线音频并收集字幕")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--wav", default=str(PROJECT_ROOT / "server/tests/fixtures/sample_en.wav"))
    ap.add_argument("--src", default="en")
    ap.add_argument("--tgt", default="zh")
    ap.add_argument("--limit-s", type=float, default=0.0, help="只发送前 N 秒音频")
    ap.add_argument("--fast", action="store_true", help="不按实时节奏发送（更快）")
    ap.add_argument("--realtime", action="store_true", help="按实时节奏发送（默认）")
    ap.add_argument("--dump", default="", help="把结果 JSON 写到该路径")
    args = ap.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
