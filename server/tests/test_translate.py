"""翻译服务单元测试：降级、错误码、成本监控（任务 1.5、FR-21、NFR-10、FM-02/03）。"""

from __future__ import annotations

import httpx
import pytest

from app.protocol import ERR_TRANSLATE_FAILED, ERR_TRANSLATE_TIMEOUT
from app.settings import Settings
from app.translate import (
    CostMonitor,
    DeepSeekTranslator,
    NullTranslator,
    TranslateOutcome,
    TranslationService,
    build_prompt,
    max_tokens_for,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """替身 httpx 客户端：返回固定响应或抛固定异常。"""

    def __init__(self, response=None, exc: Exception | None = None) -> None:
        self.response = response
        self.exc = exc
        self.calls = 0

    async def post(self, url, json=None, headers=None):  # noqa: A002
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.response


def make_settings(**kw) -> Settings:
    base = dict(_env_file=None, translate_api_key="sk-x", translate_max_retry=0)
    base.update(kw)
    return Settings(**base)


def make_translator(settings: Settings, client: FakeClient) -> DeepSeekTranslator:
    tr = DeepSeekTranslator(settings)
    tr._client = client  # 注入替身，避免真实网络
    return tr


def ok_payload(text="你好，世界") -> dict:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 9},
    }


# ---------------------------------------------------------------------------
# 提示词与上限
# ---------------------------------------------------------------------------


def test_prompt_and_max_tokens():
    p = build_prompt("en", "zh")
    assert "英语" in p and "中文" in p
    assert max_tokens_for("abc") == 64  # 下限
    assert max_tokens_for("x" * 100) == 220


# ---------------------------------------------------------------------------
# 成功 / 降级 / 超时
# ---------------------------------------------------------------------------


async def test_success_returns_translation():
    settings = make_settings()
    tr = make_translator(settings, FakeClient(FakeResponse(200, ok_payload())))
    out = await tr.translate("Hello world", "en", "zh")
    assert out.text == "你好，世界"
    assert out.degraded is False and out.error_code is None
    assert out.usage["completion_tokens"] == 9


async def test_http_error_degrades_to_source():
    settings = make_settings()
    tr = make_translator(settings, FakeClient(FakeResponse(500, text="boom")))
    out = await tr.translate("Hello world", "en", "zh")
    assert out.degraded is True
    assert out.text == "Hello world"  # 降级回传原文
    assert out.error_code == ERR_TRANSLATE_FAILED == 4001


async def test_timeout_degrades_with_4002():
    settings = make_settings()
    tr = make_translator(
        settings, FakeClient(exc=httpx.TimeoutException("timed out"))
    )
    out = await tr.translate("Hello", "en", "zh")
    assert out.degraded is True and out.error_code == ERR_TRANSLATE_TIMEOUT == 4002


async def test_empty_content_degrades():
    settings = make_settings()
    tr = make_translator(settings, FakeClient(FakeResponse(200, {"choices": [{"message": {"content": "  "}}], "usage": {}})))
    out = await tr.translate("Hello", "en", "zh")
    assert out.degraded is True


async def test_retry_count_respects_setting():
    settings = make_settings(translate_max_retry=2)
    client = FakeClient(FakeResponse(500, text="boom"))
    tr = make_translator(settings, client)
    # 为避免真实退避等待，替换 sleep
    import app.translate as T

    orig = T.asyncio.sleep
    T.asyncio.sleep = lambda *_: orig(0)
    try:
        out = await tr.translate("Hello", "en", "zh")
    finally:
        T.asyncio.sleep = orig
    assert client.calls == 3  # 首次 + 2 次重试
    assert out.degraded is True
    assert out.attempts == 3


async def test_null_translator_degrades():
    out = await NullTranslator().translate("Hi", "en", "zh")
    assert out.degraded and out.text == "Hi"


# ---------------------------------------------------------------------------
# 成本监控（FR-21 / NFR-10）
# ---------------------------------------------------------------------------


def test_cost_monitor_yuan_threshold_notifies_once_and_resets():
    mon = CostMonitor(notify_yuan=0.02, notify_tokens=10**12, price_in_per_m=2.0, price_out_per_m=8.0)
    usage = {"prompt_tokens": 1000, "completion_tokens": 1000}  # 0.01 元 / 次
    assert mon.record(usage) is None
    note = mon.record(usage)  # 累计 0.02 → 触发
    assert note is not None and "阈值" in note
    assert mon.notifications == 1
    # 触发后计数归零
    assert mon.cost_yuan == 0.0 and mon.calls == 0
    assert mon.record(usage) is None  # 重新累计，不再重复告警


def test_cost_monitor_token_threshold():
    mon = CostMonitor(notify_yuan=10**9, notify_tokens=100)
    assert mon.record({"prompt_tokens": 60, "completion_tokens": 30}) is None
    note = mon.record({"prompt_tokens": 20, "completion_tokens": 0})  # 累计 110 ≥ 100
    assert note is not None and "token" in note


async def test_translation_service_records_cost_and_exposes_notifications():
    settings = make_settings()
    mon_threshold = 0.0  # 任何一次即触发，便于断言
    svc = TranslationService(
        _SuccessTranslator(), settings
    )
    svc.cost.notify_yuan = mon_threshold
    outcome = await svc.translate("Hello", "en", "zh")
    assert outcome.degraded is False
    notes = svc.drain_notifications()
    assert notes and svc.drain_notifications() == []  # 取走后清空


async def test_translation_service_does_not_count_cost_for_degraded():
    settings = make_settings()
    svc = TranslationService(NullTranslator(), settings)
    svc.cost.notify_yuan = 0.0
    await svc.translate("Hello", "en", "zh")
    assert svc.drain_notifications() == []
    assert svc.cost.calls == 0


class _SuccessTranslator:
    async def translate(self, text, src_lang, tgt_lang) -> TranslateOutcome:
        return TranslateOutcome(
            text=f"译:{text}",
            degraded=False,
            error_code=None,
            elapsed_ms=5,
            usage={"prompt_tokens": 100, "completion_tokens": 10},
        )
