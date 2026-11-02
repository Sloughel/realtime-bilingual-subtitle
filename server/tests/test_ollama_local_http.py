"""E2 真实 HTTP 回环测试：用一个假的 Ollama 服务验证客户端实现。

与 ``test_translate_ollama.py`` 的替身客户端不同，这里**真的开一个 HTTP 服务**，
走完整的 socket → HTTP → JSON 路径，用于确认：

* 端点拼接正确（``/v1/chat/completions``、``/api/tags``）；
* 请求里确实没有 ``Authorization``（本地后端不该带凭据）；
* 服务端返回的 usage 能进入成本监控，但本地推理折算费用为 0；
* 模型缺失时的自检提示能落到日志里。

不依赖 Ollama 是否安装，可在 CI 与任意机器上运行。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.settings import Settings
from app.translate import OllamaTranslator, build_translation_service

MODEL = "qwen2.5:7b-instruct"


class FakeOllamaHandler(BaseHTTPRequestHandler):
    """最小可用的 Ollama 桩：/api/tags + /v1/chat/completions。"""

    models: list[str] = [MODEL]
    seen_auth: list[str] = []
    seen_paths: list[str] = []

    def log_message(self, *args):  # noqa: D102 - 静音访问日志
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 接口
        type(self).seen_paths.append(self.path)
        type(self).seen_auth.append(self.headers.get("Authorization", ""))
        if self.path == "/api/tags":
            self._json(200, {"models": [{"name": n} for n in self.models]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        type(self).seen_paths.append(self.path)
        type(self).seen_auth.append(self.headers.get("Authorization", ""))
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if self.path != "/v1/chat/completions":
            self._json(404, {"error": "not found"})
            return
        payload = json.loads(raw)
        text = payload["messages"][-1]["content"]
        assert payload["model"] == MODEL
        self._json(
            200,
            {
                "choices": [{"message": {"content": f"[本地译文] {text}"}}],
                "usage": {"prompt_tokens": 33, "completion_tokens": 7},
            },
        )


@pytest.fixture
def fake_ollama():
    FakeOllamaHandler.models = [MODEL]
    FakeOllamaHandler.seen_auth = []
    FakeOllamaHandler.seen_paths = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def ollama_settings(base_url: str, model: str = MODEL) -> Settings:
    return Settings(
        _env_file=None,
        translate_backend="ollama",
        ollama_base_url=base_url,
        ollama_model=model,
        translate_max_retry=0,
    )


async def test_translate_over_real_http(fake_ollama, caplog):
    service = build_translation_service(ollama_settings(fake_ollama))
    assert isinstance(service.translator, OllamaTranslator)

    await service.warm_up()
    outcome = await service.translate("Hello world.", "en", "zh")

    assert outcome.degraded is False
    assert outcome.text == "[本地译文] Hello world."
    assert outcome.error_code is None

    # 端点被真实访问，且请求未携带任何凭据
    assert "/api/tags" in FakeOllamaHandler.seen_paths
    assert "/v1/chat/completions" in FakeOllamaHandler.seen_paths
    assert all(auth == "" for auth in FakeOllamaHandler.seen_auth)

    # 本地推理：token 计数照常，费用为 0
    summary = service.cost.summary()
    assert summary["calls"] == 1
    assert summary["tokens_in"] == 33
    assert summary["cost_yuan"] == 0.0

    await service.aclose()


async def test_warm_up_warns_for_missing_model(fake_ollama, caplog):
    FakeOllamaHandler.models = ["llama3.2:3b"]
    translator = OllamaTranslator(ollama_settings(fake_ollama, model="qwen2.5:7b-instruct"))
    import logging

    with caplog.at_level(logging.WARNING):
        await translator.warm_up()
    assert f"ollama pull {MODEL}" in caplog.text
    await translator.aclose()
