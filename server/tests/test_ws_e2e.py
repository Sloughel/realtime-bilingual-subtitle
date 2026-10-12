"""WebSocket 端到端集成测试（任务 1.2 / 1.6）。

用替身 ASR 与翻译避免加载模型与联网，验证：握手、配置、音频帧、断句、
字幕 JSON 结构、错误码、非法帧断开、协议版本校验、心跳。
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.translate import TranslateOutcome, TranslationService

from conftest import make_frames

SR = 16000


class StubASR:
    device = "cuda"
    degraded = False

    def __init__(self) -> None:
        self.calls = 0

    def status(self) -> dict:
        return {"device": "stub", "degraded": False}

    async def transcribe_async(self, pcm, language: str) -> str:
        self.calls += 1
        return f"stub sentence {self.calls}"


class StubTranslator:
    def __init__(self) -> None:
        self.calls = 0

    async def translate(self, text: str, src: str, tgt: str) -> TranslateOutcome:
        self.calls += 1
        return TranslateOutcome(
            text=f"译文:{text}",
            degraded=False,
            error_code=None,
            elapsed_ms=4,
            usage={"prompt_tokens": 20, "completion_tokens": 8},
        )


@pytest.fixture
def app(settings):
    asr = StubASR()
    service = TranslationService(StubTranslator(), settings)
    return create_app(settings, asr=asr, translation=service, load_model=False)


def one_sentence_audio(fixture_pcm16: np.ndarray) -> np.ndarray:
    """0.5s 静音 + 真实语音 1.4–4.2s + 1.0s 静音 → 恰好一句。"""
    return np.concatenate(
        [
            np.zeros(int(0.5 * SR), dtype=np.int16),
            fixture_pcm16[int(1.4 * SR) : int(4.2 * SR)],
            np.zeros(int(1.0 * SR), dtype=np.int16),
        ]
    )


def drain_until(app_ws, want: str, max_pings: int = 8) -> list[dict]:
    """发送 ping 作为节拍，收集消息直到出现 ``want`` 类型（或达到上限）。

    这样既不依赖异步推送顺序，也保证每次 ``receive`` 都有消息可读，不会挂起。
    """
    collected: list[dict] = []
    for _ in range(max_pings):
        app_ws.send_json({"type": "ping", "t": 1})
        msg = app_ws.receive_json()
        while msg.get("type") != "pong":
            collected.append(msg)
            if msg.get("type") == want:
                return collected
            msg = app_ws.receive_json()
    return collected


def test_websocket_full_flow(app, fixture_pcm16):
    frames = make_frames(one_sentence_audio(fixture_pcm16))
    with TestClient(app) as client:
        with client.websocket_connect("/ws?protocol_version=1&session_id=t-flow") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["protocol_version"] == 1
            assert hello["limits"]["chunk_ms"] == 100

            ws.send_json({"type": "config", "mode": "mic", "src_lang": "en", "tgt_lang": "zh"})
            ack = ws.receive_json()
            assert ack["type"] == "config_ack" and ack["ok"] is True
            assert ack["applied"]["src_lang"] == "en"

            ws.send_json({"type": "start"})
            for f in frames:
                ws.send_bytes(f)
            ws.send_json({"type": "stop"})

            msgs = drain_until(ws, "subtitle")
            subs = [m for m in msgs if m.get("type") == "subtitle"]
            assert subs, f"未收到字幕，收到：{[m.get('type') for m in msgs]}"

            s = subs[0]
            assert s["source"].startswith("stub sentence")
            assert s["translation"].startswith("译文:")
            assert s["src_lang"] == "en" and s["tgt_lang"] == "zh"
            assert s["sentence_id"] == 1
            assert s["seq_start"] >= 1 and s["seq_end"] >= s["seq_start"]
            assert s["end_ms"] > s["start_ms"] >= 0
            assert s["asr_ms"] >= 0 and s["translate_ms"] >= 0
            assert "created_ms" in s


def test_status_endpoint_aggregates_latency(app, fixture_pcm16):
    frames = make_frames(one_sentence_audio(fixture_pcm16))
    with TestClient(app) as client:
        with client.websocket_connect("/ws?protocol_version=1&session_id=t-status") as ws:
            ws.receive_json()
            ws.send_json({"type": "config", "src_lang": "en", "tgt_lang": "zh"})
            ws.receive_json()
            ws.send_json({"type": "start"})
            for f in frames:
                ws.send_bytes(f)
            ws.send_json({"type": "stop"})
            drain_until(ws, "subtitle")

        status = client.get("/status").json()
        assert status["sessions"] == 1
        assert status["latency"]["sentences"] >= 1
        assert "cost" in status

        health = client.get("/health").json()
        assert health["status"] == "ok"
        assert health["asr"]["device"] == "stub"


def test_protocol_version_rejected(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws?protocol_version=9") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["code"] == 1002
            assert msg["recoverable"] is False


def test_bad_config_returns_1001(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws?protocol_version=1") as ws:
            ws.receive_json()
            ws.send_json({"type": "config", "mode": "camera"})
            msg = ws.receive_json()
            assert msg["type"] == "error" and msg["code"] == 1001


def test_unknown_message_type_returns_1001(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws?protocol_version=1") as ws:
            ws.receive_json()
            ws.send_json({"type": "frobnicate"})
            msg = ws.receive_json()
            assert msg["type"] == "error" and msg["code"] == 1001


def test_ping_pong(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws?protocol_version=1") as ws:
            ws.receive_json()
            ws.send_json({"type": "ping", "t": 1757318400000})
            msg = ws.receive_json()
            assert msg == {"type": "pong", "t": 1757318400000}


def test_ten_invalid_frames_close_connection(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws?protocol_version=1") as ws:
            ws.receive_json()
            ws.send_json({"type": "config", "src_lang": "en", "tgt_lang": "zh"})
            ws.receive_json()
            ws.send_json({"type": "start"})
            for _ in range(10):
                ws.send_bytes(b"bad-frame")
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["code"] == 2001
