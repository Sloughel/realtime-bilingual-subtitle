"""翻译服务：``Translator`` 抽象 + DeepSeek（OpenAI 兼容）实现 + 成本监控。

对应需求：FR-07 翻译、FR-21 成本监控、NFR-10 成本阈值；故障处理 FM-02（API 错误）、
FM-03（限流/超时）。参数见 03 文档 §9.2，prompt 见 03 文档 §10。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Protocol

import httpx

from .protocol import (
    ERR_TRANSLATE_FAILED,
    ERR_TRANSLATE_TIMEOUT,
    LANG_NAMES,
)
from .settings import Settings
from .telemetry import get_logger

log = get_logger("translate")

# 与 03 文档 §10 一致的字幕翻译提示词
SYSTEM_PROMPT = (
    "你是一名专业字幕翻译。将用户给出的{src_name}音频转写文本翻译成{tgt_name}。\n"
    "要求：\n"
    "1. 只输出译文，不要任何解释、前缀或引用原文；\n"
    "2. 人名、地名、品牌等专有名词保留原文或使用通用译法；\n"
    "3. 译文口语化、简短，符合字幕习惯；\n"
    "4. 不要逐字硬译语气词（如嗯、啊）。"
)


class TranslateOutcome(NamedTuple):
    text: str
    degraded: bool
    error_code: int | None
    elapsed_ms: int
    usage: dict[str, Any]
    attempts: int = 1


class Translator(Protocol):
    """翻译后端抽象（03 文档 §9.2）。新增后端（如 Ollama，E2）实现本协议即可。"""

    async def translate(self, text: str, src_lang: str, tgt_lang: str) -> TranslateOutcome:
        ...


def build_prompt(src_lang: str, tgt_lang: str) -> str:
    return SYSTEM_PROMPT.format(
        src_name=LANG_NAMES.get(src_lang, src_lang),
        tgt_name=LANG_NAMES.get(tgt_lang, tgt_lang),
    )


def max_tokens_for(text: str) -> int:
    return max(64, 2 * len(text) + 20)


# ---------------------------------------------------------------------------
# 成本监控（FR-21 / NFR-10）
# ---------------------------------------------------------------------------


@dataclass
class CostMonitor:
    """逐句累计 token 与折算费用；达到阈值返回一次通知消息并重置计数。"""

    notify_yuan: float = 10.0
    notify_tokens: int = 1_000_000_000
    price_in_per_m: float = 2.0
    price_out_per_m: float = 8.0
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_yuan: float = 0.0
    notifications: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def record(self, usage: dict[str, Any]) -> str | None:
        """记一次调用；若达到阈值返回通知文案（否则 None）。"""
        tokens_in = int(usage.get("prompt_tokens") or 0)
        tokens_out = int(usage.get("completion_tokens") or 0)
        self.calls += 1
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.cost_yuan += (
            tokens_in / 1e6 * self.price_in_per_m
            + tokens_out / 1e6 * self.price_out_per_m
        )

        total_tokens = self.tokens_in + self.tokens_out
        hit_yuan = self.cost_yuan >= self.notify_yuan
        hit_tokens = total_tokens >= self.notify_tokens
        if not (hit_yuan or hit_tokens):
            return None

        reason = "金额" if hit_yuan else "token 数"
        msg = (
            f"翻译成本累计达阈值（{reason}）：约 {self.cost_yuan:.2f} 元 / "
            f"{total_tokens} tokens / {self.calls} 句，计数已重置。"
        )
        self.notifications += 1
        log.warning("FR-21 成本通知：%s", msg)
        # 重置计数，避免重复告警（NFR-10）
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_yuan = 0.0
        return msg

    def summary(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_yuan": round(self.cost_yuan, 4),
            "notifications": self.notifications,
        }


# ---------------------------------------------------------------------------
# DeepSeek（OpenAI 兼容）实现
# ---------------------------------------------------------------------------


class NullTranslator:
    """未配置密钥时的占位实现：直接降级回传原文（FM-02）。"""

    async def translate(self, text: str, src_lang: str, tgt_lang: str) -> TranslateOutcome:
        return TranslateOutcome(
            text=text,
            degraded=True,
            error_code=ERR_TRANSLATE_FAILED,
            elapsed_ms=0,
            usage={},
        )


class DeepSeekTranslator:
    """OpenAI 兼容 Chat Completions 客户端（默认 DeepSeek）。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.translate_base_url.rstrip("/")
        self.model = settings.translate_model
        self.timeout = settings.translate_timeout_s
        self.max_retry = settings.translate_max_retry
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            # 句子间隔通常 8~15s，httpx 默认 keepalive_expiry 只有 5s，
            # 会导致频繁重新建连/TLS 握手，放大翻译延迟抖动。
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(
                    max_keepalive_connections=5,
                    keepalive_expiry=60.0,
                ),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def warm_up(self) -> None:
        """预热 HTTP/TLS 连接，避免首句翻译承担建连开销（实测可差 4s 以上）。

        只做一次 GET，不消耗 token；任何异常均忽略。
        """
        client = self._get_client()
        t0 = time.perf_counter()
        try:
            await client.get(
                self.base_url,
                headers={"Authorization": f"Bearer {self.settings.translate_api_key.strip()}"},
            )
        except Exception as exc:  # noqa: BLE001 - 预热失败不影响功能
            log.debug("翻译端点预热未完成（可忽略）：%s", exc)
        else:
            log.info("翻译端点连接预热完成：%.0f ms", (time.perf_counter() - t0) * 1000)

    async def translate(self, text: str, src_lang: str, tgt_lang: str) -> TranslateOutcome:
        if not text:
            return TranslateOutcome("", False, None, 0, {})

        payload = {
            "model": self.model,
            "temperature": 0.3,
            "max_tokens": max_tokens_for(text),
            "messages": [
                {"role": "system", "content": build_prompt(src_lang, tgt_lang)},
                {"role": "user", "content": text},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.settings.translate_api_key.strip()}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        client = self._get_client()
        last_error_code = ERR_TRANSLATE_FAILED
        last_message = ""

        for attempt in range(self.max_retry + 1):
            t0 = time.perf_counter()
            try:
                resp = await client.post(url, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                last_error_code = ERR_TRANSLATE_TIMEOUT
                last_message = f"请求超时（>{self.timeout}s）：{exc}"
            except httpx.HTTPError as exc:
                last_error_code = ERR_TRANSLATE_FAILED
                last_message = f"网络错误：{exc}"
            else:
                if resp.status_code == 200:
                    data = resp.json()
                    out = (data["choices"][0]["message"]["content"] or "").strip()
                    elapsed = int((time.perf_counter() - t0) * 1000)
                    usage = data.get("usage") or {}
                    if not out:
                        last_error_code = ERR_TRANSLATE_FAILED
                        last_message = "返回内容为空"
                    else:
                        if elapsed > 2000:
                            log.warning(
                                "翻译慢响应：%dms（第 %d 次尝试）", elapsed, attempt + 1
                            )
                        return TranslateOutcome(
                            out, False, None, elapsed, usage, attempt + 1
                        )
                elif resp.status_code == 429:
                    last_error_code = ERR_TRANSLATE_FAILED
                    last_message = "触发限流（429）"
                elif resp.status_code == 401:
                    last_error_code = ERR_TRANSLATE_FAILED
                    last_message = "鉴权失败（401）：密钥无效或已撤销"
                elif resp.status_code == 402:
                    last_error_code = ERR_TRANSLATE_FAILED
                    last_message = "余额不足（402）"
                else:
                    last_error_code = ERR_TRANSLATE_FAILED
                    last_message = f"HTTP {resp.status_code}: {resp.text[:160]}"

            log.warning(
                "翻译失败（第 %d/%d 次）：%s", attempt + 1, self.max_retry + 1, last_message
            )
            if attempt < self.max_retry:
                await asyncio.sleep(2 ** attempt)  # 1s / 2s 指数退避（FM-02）

        # 全部重试失败 → 降级回传原文（FM-02 / FM-03）
        return TranslateOutcome(text, True, last_error_code, 0, {}, attempt + 1)


# ---------------------------------------------------------------------------
# 服务层：并发控制 + 成本监控 + 降级
# ---------------------------------------------------------------------------


class TranslationService:
    """对上层暴露单一入口，内部处理并发上限、成本累计与降级。"""

    def __init__(self, translator: Translator, settings: Settings) -> None:
        self.translator = translator
        self.settings = settings
        self.cost = CostMonitor(
            notify_yuan=settings.cost_notify_yuan,
            notify_tokens=settings.cost_notify_tokens,
            price_in_per_m=settings.price_in_per_m,
            price_out_per_m=settings.price_out_per_m,
        )
        self._sem = asyncio.Semaphore(max(1, settings.translate_concurrency))
        self.notifications: list[str] = []

    async def translate(self, text: str, src_lang: str, tgt_lang: str) -> TranslateOutcome:
        async with self._sem:  # 并发上限（02 §4.1）
            outcome = await self.translator.translate(text, src_lang, tgt_lang)
        if not outcome.degraded and outcome.usage:
            note = self.cost.record(outcome.usage)
            if note:
                self.notifications.append(note)
        return outcome

    def drain_notifications(self) -> list[str]:
        notes, self.notifications = self.notifications, []
        return notes

    async def warm_up(self) -> None:
        warm = getattr(self.translator, "warm_up", None)
        if warm is not None:
            await warm()

    async def aclose(self) -> None:
        aclose = getattr(self.translator, "aclose", None)
        if aclose is not None:
            await aclose()


def build_translation_service(settings: Settings) -> TranslationService:
    if settings.translate_configured:
        translator: Translator = DeepSeekTranslator(settings)
    else:
        log.warning("未配置 TRANSLATE_API_KEY，翻译将一律降级回传原文（FM-02）")
        translator = NullTranslator()
    return TranslationService(translator, settings)
