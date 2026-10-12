"""faster-whisper 封装（FR-06、03 文档 §9.1、02 文档 §4.1）。

设计要点：

* 在导入 ``faster_whisper`` **之前**注册 CUDA 运行时 DLL（见 :mod:`app.dll`）；
* **串行执行**：GPU 推理独占模型实例，用锁保证同一时刻只有一句在推理；
* **GPU 不可用时降级 CPU**（``int8``）并显著提示，不静默降级（FM-04）；
* 模型缺失或损坏视为致命，抛出含修复命令的错误（FM-05）；
* 空结果或推理异常由调用方丢弃该句并继续（FM-08）。
"""

from __future__ import annotations

import asyncio
import threading
import time

import numpy as np

from . import dll

dll.register()  # 必须在导入 ctranslate2 / faster_whisper 之前

from .settings import Settings
from .telemetry import get_logger

log = get_logger("asr")


class ModelUnavailableError(RuntimeError):
    """模型缺失或损坏（FM-05）。"""


class ASREngine:
    """faster-whisper 推理封装。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model = None
        self.device = settings.whisper_device
        self.compute_type = settings.whisper_compute_type
        self.degraded = False  # 是否由 GPU 降级到 CPU
        self.degraded_reason: str | None = None
        self.load_ms: int = 0
        self._sample_rate = 16000
        self._lock = threading.Lock()
        self._infer_count = 0
        self._infer_total_ms = 0.0

    # ------------------------------------------------------------------
    def load(self) -> None:
        """加载模型。失败时抛 :class:`ModelUnavailableError`（FM-05）。"""
        from faster_whisper import WhisperModel

        model_path = self.settings.whisper_model_path
        t0 = time.perf_counter()
        try:
            self.model = WhisperModel(
                model_path, device=self.device, compute_type=self.compute_type
            )
        except Exception as exc:  # noqa: BLE001 - 覆盖 CUDA/cuDNN/模型加载各类异常
            msg = str(exc)
            log.error("模型加载失败（device=%s）：%s", self.device, msg)
            if self.device != "cpu":
                # FM-04：GPU 不可用 → 降级 CPU，并显著提示
                log.warning(
                    "GPU 推理不可用，自动降级为 CPU（%s）。"
                    "注意：CPU 模式延迟可能不满足 NFR-01 的 800ms/句预算，"
                    "客户端将收到识别降级提示。",
                    self.settings.whisper_cpu_compute_type,
                )
                self.device = "cpu"
                self.compute_type = self.settings.whisper_cpu_compute_type
                self.degraded = True
                self.degraded_reason = f"GPU 不可用：{msg[:200]}"
                try:
                    self.model = WhisperModel(
                        model_path, device="cpu", compute_type=self.compute_type
                    )
                except Exception as exc2:  # noqa: BLE001
                    raise ModelUnavailableError(
                        self._missing_hint(model_path, exc2)
                    ) from exc2
            else:
                raise ModelUnavailableError(self._missing_hint(model_path, exc)) from exc
        self.load_ms = int((time.perf_counter() - t0) * 1000)
        log.info(
            "whisper 模型已加载：%s（device=%s, compute=%s, %d ms）",
            model_path,
            self.device,
            self.compute_type,
            self.load_ms,
        )
        # 预热：首次推理含 CUDA 上下文与 kernel 初始化，若不预热会把冷启动耗时算到
        # 第一句真实字幕上，污染 NFR-01 的 800ms/句预算统计。
        try:
            warm_t0 = time.perf_counter()
            self.transcribe(np.zeros(self._sample_rate, dtype=np.float32), "en")
            log.info("模型预热完成：%d ms", int((time.perf_counter() - warm_t0) * 1000))
            self._infer_count = 0  # 预热不计入统计
            self._infer_total_ms = 0.0
        except Exception as exc:  # noqa: BLE001 - 预热失败不应阻断启动
            log.warning("模型预热失败（不影响启动）：%s", exc)

    def _missing_hint(self, model_path: str, exc: Exception) -> str:
        return (
            f"whisper 模型加载失败：{model_path}\n"
            f"原始错误：{exc}\n"
            "若模型缺失或损坏，请执行：\n"
            "  .venv/Scripts/python.exe scripts/download_model.py\n"
            "（FM-05：模型缺失为阻断项，服务将停止接受音频）"
        )

    # ------------------------------------------------------------------
    def transcribe(self, pcm: np.ndarray, language: str) -> str:
        """对整句 PCM（float32、16kHz、单声道）转写，返回纯文本。"""
        if self.model is None:
            raise ModelUnavailableError("模型未加载")
        with self._lock:  # 串行队列：GPU 独占
            t0 = time.perf_counter()
            segments, _info = self.model.transcribe(
                pcm,
                language=language,
                beam_size=1,
                vad_filter=False,  # 断句已由上游完成（03 §9.1）
                condition_on_previous_text=False,
                task="transcribe",
            )
            text = "".join(seg.text for seg in segments).strip()
            elapsed = (time.perf_counter() - t0) * 1000
        self._infer_count += 1
        self._infer_total_ms += elapsed
        log.debug("ASR %.0f ms → %r", elapsed, text[:60])
        return text

    async def transcribe_async(self, pcm: np.ndarray, language: str) -> str:
        """线程池中执行，避免阻塞事件循环。"""
        return await asyncio.to_thread(self.transcribe, pcm, language)

    # ------------------------------------------------------------------
    def status(self) -> dict[str, object]:
        return {
            "device": self.device,
            "compute_type": self.compute_type,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "load_ms": self.load_ms,
            "avg_ms": round(self._infer_total_ms / self._infer_count, 1)
            if self._infer_count
            else 0.0,
        }
