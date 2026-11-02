"""E1 接入鉴权测试：令牌来源、定长比较、错误码 1003、关闭码 4401。

对应 01 文档 FR-13（互联网连接）与 03 文档 §2/§6：公网场景下 ``/ws`` 必须鉴权，
未通过鉴权的连接不分配会话、不产生任何识别开销。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.connection import extract_token, token_ok
from app.main import create_app
from app.protocol import ERR_UNAUTHORIZED, WS_CLOSE_UNAUTHORIZED
from app.settings import Settings
from app.translate import TranslateOutcome, TranslationService

TOKEN = "s3cret-token-for-test"


class StubASR:
    device = "cpu"
    degraded = False

    def status(self) -> dict:
        return {"device": "stub", "degraded": False}

    async def transcribe_async(self, pcm, language: str) -> str:
        return "stub"


class StubTranslator:
    async def translate(self, text: str, src: str, tgt: str) -> TranslateOutcome:
        return TranslateOutcome(f"译文:{text}", False, None, 1, {})


def make_app(**overrides) -> TestClient:
    settings = Settings(
        _env_file=None,
        whisper_device="cpu",
        translate_max_retry=0,
        **overrides,
    )
    app = create_app(
        settings,
        asr=StubASR(),
        translation=TranslationService(StubTranslator(), settings),
        load_model=False,
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# 纯函数：令牌解析与比较
# ---------------------------------------------------------------------------


def test_extract_token_prefers_query():
    assert extract_token("abc", "Bearer xyz") == "abc"
    assert extract_token(None, "Bearer xyz") == "xyz"
    assert extract_token(None, "bearer  xyz ") == "xyz"
    assert extract_token(None, None) == ""
    assert extract_token("  ", None) == ""


def test_token_ok_disabled_when_not_configured():
    settings = Settings(_env_file=None, auth_token="")
    assert settings.auth_required is False
    assert token_ok(settings, "") is True
    assert token_ok(settings, "anything") is True


def test_token_ok_requires_exact_match():
    settings = Settings(_env_file=None, auth_token=f"  {TOKEN}  ")
    assert settings.auth_required is True
    assert token_ok(settings, TOKEN) is True
    assert token_ok(settings, "wrong") is False
    assert token_ok(settings, "") is False


# ---------------------------------------------------------------------------
# 端到端：WebSocket 连接
# ---------------------------------------------------------------------------


def test_ws_rejects_missing_token():
    client = make_app(auth_token=TOKEN)
    with client:
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["code"] == ERR_UNAUTHORIZED
            assert msg["recoverable"] is False
            # 服务端随后以 4401 关闭，客户端据此提示"令牌错误"而不是无限重连
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == WS_CLOSE_UNAUTHORIZED


def test_ws_rejects_wrong_token():
    client = make_app(auth_token=TOKEN)
    with client:
        with client.websocket_connect("/ws?token=not-the-token") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error" and msg["code"] == ERR_UNAUTHORIZED
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == WS_CLOSE_UNAUTHORIZED


def test_ws_accepts_query_token():
    client = make_app(auth_token=TOKEN)
    with client:
        with client.websocket_connect(f"/ws?protocol_version=1&token={TOKEN}") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["protocol_version"] == 1


def test_ws_accepts_bearer_header():
    client = make_app(auth_token=TOKEN)
    with client:
        with client.websocket_connect(
            "/ws", headers={"Authorization": f"Bearer {TOKEN}"}
        ) as ws:
            assert ws.receive_json()["type"] == "hello"


def test_ws_open_when_auth_disabled():
    client = make_app(auth_token="")
    with client:
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"


def test_health_reports_auth_and_backend():
    client = make_app(auth_token=TOKEN)
    with client:
        body = client.get("/health").json()
    assert body["auth_required"] is True
    assert body["tls"] is False
    assert body["translate_backend"] == "deepseek"
    # 令牌与密钥绝不出现在健康检查响应里
    assert TOKEN not in str(body)
