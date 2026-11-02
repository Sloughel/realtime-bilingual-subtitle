"""E2 本地模型翻译测试：Ollama 端点、无鉴权头、模型自检、零计费。

E2 的目标是"音频与文本全链路不出内网"（01 文档 FR-14 / NFR-11），
因此这里重点验证三件事：请求确实打到本地端点、请求里没有密钥头、
成本监控不会把本地推理算成钱。
"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.protocol import ERR_TRANSLATE_FAILED
from app.settings import Settings
from app.translate import (
    DeepSeekTranslator,
    NullTranslator,
    OllamaTranslator,
    build_translation_service,
)


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """同时支持 GET（预热/自检）与 POST（翻译）的替身客户端。"""

    def __init__(self, post_response=None, get_response=None, exc: Exception | None = None):
        self.post_response = post_response
        self.get_response = get_response
        self.exc = exc
        self.posts: list[dict] = []
        self.gets: list[dict] = []

    async def post(self, url, json=None, headers=None):  # noqa: A002
        self.posts.append({"url": url, "json": json, "headers": headers or {}})
        if self.exc is not None:
            raise self.exc
        return self.post_response

    async def get(self, url, headers=None):
        self.gets.append({"url": url, "headers": headers or {}})
        if self.exc is not None:
            raise self.exc
        return self.get_response


def make_settings(**kw) -> Settings:
    base = dict(
        _env_file=None,
        translate_backend="ollama",
        ollama_model="qwen2.5:7b-instruct",
        ollama_base_url="http://127.0.0.1:11434",
        translate_max_retry=0,
    )
    base.update(kw)
    return Settings(**base)


def chat_payload(text: str = "你好，世界") -> dict:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 10},
    }


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def test_ollama_backend_needs_no_api_key():
    settings = make_settings(translate_api_key="")
    assert settings.translate_is_local is True
    assert settings.translate_configured is True  # 本地模型无需密钥
    assert settings.translate_model_name == "qwen2.5:7b-instruct"


def test_backend_value_is_normalised_and_validated():
    assert make_settings(translate_backend=" Ollama ").translate_backend == "ollama"
    with pytest.raises(ValidationError):
        make_settings(translate_backend="openai-azure")


def test_cloud_backend_still_requires_key():
    settings = Settings(_env_file=None, translate_backend="deepseek", translate_api_key="")
    assert settings.translate_is_local is False
    assert settings.translate_configured is False


# ---------------------------------------------------------------------------
# 请求形态
# ---------------------------------------------------------------------------


async def test_translator_targets_local_openai_endpoint_without_auth_header():
    settings = make_settings()
    client = FakeClient(post_response=FakeResponse(200, chat_payload()))
    translator = OllamaTranslator(settings)
    translator._client = client

    outcome = await translator.translate("Hello there.", "en", "zh")

    assert outcome.degraded is False
    assert outcome.text == "你好，世界"
    assert outcome.usage["prompt_tokens"] == 40
    call = client.posts[0]
    assert call["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert call["json"]["model"] == "qwen2.5:7b-instruct"
    # 关键：本地请求不带任何凭据
    assert "Authorization" not in call["headers"]


async def test_translator_degrades_on_http_error():
    settings = make_settings()
    translator = OllamaTranslator(settings)
    translator._client = FakeClient(post_response=FakeResponse(500, text="boom"))

    outcome = await translator.translate("Hello.", "en", "zh")
    assert outcome.degraded is True
    assert outcome.error_code == ERR_TRANSLATE_FAILED
    assert outcome.text == "Hello."  # 降级回传原文（FM-02）


# ---------------------------------------------------------------------------
# 启动自检（warm_up）
# ---------------------------------------------------------------------------


async def test_warm_up_warns_when_model_missing(caplog):
    translator = OllamaTranslator(make_settings())
    translator._client = FakeClient(
        get_response=FakeResponse(200, {"models": [{"name": "llama3.2:3b"}]})
    )
    with caplog.at_level(logging.WARNING):
        await translator.warm_up()
    assert "ollama pull" in caplog.text


async def test_warm_up_silent_when_model_present(caplog):
    translator = OllamaTranslator(make_settings())
    translator._client = FakeClient(
        get_response=FakeResponse(200, {"models": [{"name": "qwen2.5:7b-instruct"}]})
    )
    with caplog.at_level(logging.INFO):
        await translator.warm_up()
    assert "预热完成" in caplog.text
    assert "ollama pull" not in caplog.text


async def test_warm_up_never_raises_when_service_down(caplog):
    translator = OllamaTranslator(make_settings())
    translator._client = FakeClient(exc=OSError("connection refused"))
    with caplog.at_level(logging.WARNING):
        await translator.warm_up()
    assert "不可达" in caplog.text


# ---------------------------------------------------------------------------
# 工厂与计费
# ---------------------------------------------------------------------------


async def test_factory_builds_ollama_service_without_billing():
    settings = make_settings()
    service = build_translation_service(settings)
    assert isinstance(service.translator, OllamaTranslator)
    assert service.backend_name == "ollama"

    service.translator._client = FakeClient(post_response=FakeResponse(200, chat_payload()))

    outcome = await service.translate("Hello.", "en", "zh")
    assert outcome.degraded is False
    summary = service.cost.summary()
    assert summary["calls"] == 1
    assert summary["tokens_in"] == 40
    assert summary["cost_yuan"] == 0.0  # 本地推理不计费


def test_factory_still_builds_deepseek_and_null():
    cloud = make_settings(translate_backend="deepseek", translate_api_key="sk-real-looking")
    assert isinstance(build_translation_service(cloud).translator, DeepSeekTranslator)

    no_key = make_settings(translate_backend="deepseek", translate_api_key="")
    service = build_translation_service(no_key)
    assert isinstance(service.translator, NullTranslator)
