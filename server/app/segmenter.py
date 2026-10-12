"""Silero VAD 流式断句器（FR-05、03 文档 §9.3、02 文档 §4.2）。

与 faster-whisper 自带实现的差异（**实现要点，已在 M1 实测核对**）：

* ``faster_whisper.vad.get_speech_timestamps()`` 面向**离线整段音频**，不适用于流式；
* ``faster_whisper.vad.SileroVADModel.__call__`` 每次调用都会把 LSTM 状态
  ``h``/``c`` 重置为零，因此它**并非跨调用有状态**——若按 512 采样点分块直接调用，
  每块都会以零状态推理，丢失时间上下文。

故本模块直接持有 ONNX 会话，自行保持 ``h``/``c`` 与 64 采样点前置上下文，
按 512 采样点（32ms @16kHz）逐块推理得到语音概率，再累计静音时长判定切句。
参数语义与 ``VadOptions`` 字段的对应见 03 文档 §9.3。
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .settings import Settings
from .telemetry import get_logger

log = get_logger("segmenter")

# Silero VAD v5/v6 @16kHz 固定窗口与前置上下文（见 faster_whisper/vad.py 的实现）
VAD_WINDOW_SAMPLES = 512
VAD_CONTEXT_SAMPLES = 64
# 句长上限处容忍的最短静音（ms），与 faster_whisper.vad 保持一致
MIN_SILENCE_AT_MAX_MS = 98


@dataclass
class Segment:
    """一句断句结果。"""

    pcm: np.ndarray  # float32, [-1, 1]，16kHz 单声道
    start_ms: int
    end_ms: int
    seq_start: int
    seq_end: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class StreamingVADModel:
    """跨块保持 LSTM 状态的 Silero VAD 包装。

    ``session`` 为 ``onnxruntime.InferenceSession``（取自
    ``faster_whisper.vad.get_vad_model().session``）。
    """

    def __init__(self, session) -> None:
        self.session = session

    def reset(self) -> None:
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, VAD_CONTEXT_SAMPLES), dtype=np.float32)

    def prob(self, chunk: np.ndarray) -> float:
        """对单个 512 采样点窗口返回语音概率，并推进内部状态。"""
        if not hasattr(self, "h"):
            self.reset()
        x = np.ascontiguousarray(chunk, dtype=np.float32).reshape(1, -1)
        fed = np.concatenate([self.context, x], axis=1)  # (1, 64+512)
        self.context = np.ascontiguousarray(x[..., -VAD_CONTEXT_SAMPLES:])
        out, self.h, self.c = self.session.run(
            None, {"input": fed, "h": self.h, "c": self.c}
        )
        return float(np.asarray(out).reshape(-1)[0])

    # 兼容 faster-whisper 模型对象：允许直接传入 SileroVADModel
    __call__ = prob


def _default_vad_factory() -> StreamingVADModel:
    from faster_whisper.vad import get_vad_model

    return StreamingVADModel(get_vad_model().session)


class Segmenter:
    """流式断句器：输入 PCM16 分块，输出整句音频。

    用法::

        seg = Segmenter.from_settings(settings)
        for frame in frames:
            for sentence in seg.feed(frame.pcm, frame.seq):
                ...
        tail = seg.flush()   # 收到 stop 时冲刷当前句（FR-09）
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        min_silence_ms: int = 500,
        min_speech_ms: int = 300,
        max_sentence_ms: int = 15000,
        threshold: float = 0.5,
        neg_threshold: float | None = None,
        pre_roll_ms: int = 200,
        vad_factory: Callable[[], "StreamingVADModel"] | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.neg_threshold = (
            neg_threshold if neg_threshold is not None else max(threshold - 0.15, 0.01)
        )
        self.min_speech_samples = int(sample_rate * min_speech_ms / 1000)
        self.min_silence_samples = int(sample_rate * min_silence_ms / 1000)
        self.max_sentence_samples = int(sample_rate * max_sentence_ms / 1000)
        self.pre_roll_samples = int(sample_rate * pre_roll_ms / 1000)
        self._min_silence_at_max = int(sample_rate * MIN_SILENCE_AT_MAX_MS / 1000)
        self._vad_factory = vad_factory or _default_vad_factory
        self._model: StreamingVADModel | None = None
        self.reset()

    @classmethod
    def from_settings(cls, settings: Settings, **overrides) -> "Segmenter":
        params = dict(
            sample_rate=16000,
            min_silence_ms=settings.vad_min_silence_ms,
            min_speech_ms=settings.vad_min_speech_ms,
            max_sentence_ms=settings.vad_max_sentence_ms,
            threshold=settings.vad_threshold,
            neg_threshold=settings.vad_neg_threshold,
            pre_roll_ms=settings.vad_pre_roll_ms,
        )
        params.update(overrides)
        return cls(**params)

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """丢弃当前未断句的音频（对应 ``reset`` 控制消息）。"""
        if self._model is not None:
            self._model.reset()
        self._residual = np.zeros(0, dtype=np.float32)
        self._preroll = np.zeros(0, dtype=np.float32)
        self._buf: list[np.ndarray] = []
        self._triggered = False
        self._seg_start = 0  # 当前句在音频流中的起始采样点
        self._last_speech_end = 0  # 最近一次判定为语音的窗口末端
        self._silence_run = 0
        self._silence_start: int | None = None
        self._processed = 0  # 已被 VAD 处理过的采样点总数
        self._pos = 0  # 已喂入的采样点总数
        self._frame_spans: list[tuple[int, int, int]] = []  # (start, end, seq)

    # ------------------------------------------------------------------
    def _ensure_model(self) -> StreamingVADModel:
        if self._model is None:
            self._model = self._vad_factory()
            self._model.reset()
        return self._model

    def _ms(self, sample_index: int) -> int:
        return int(round(sample_index * 1000 / self.sample_rate))

    def _seq_at(self, sample_index: int) -> int:
        if not self._frame_spans:
            return 0
        starts = [s for s, _e, _q in self._frame_spans]
        idx = bisect.bisect_right(starts, sample_index) - 1
        idx = max(0, min(idx, len(self._frame_spans) - 1))
        return self._frame_spans[idx][2]

    # ------------------------------------------------------------------
    def feed(self, pcm_int16: np.ndarray, seq: int) -> list[Segment]:
        """喂入一帧 PCM16，返回本次可能产出的完整句子（通常 0 或 1 条）。"""
        model = self._ensure_model()
        samples = np.asarray(pcm_int16, dtype=np.float32) / 32768.0
        start = self._pos
        self._pos += samples.size
        self._frame_spans.append((start, self._pos, seq))
        self._residual = (
            np.concatenate([self._residual, samples])
            if self._residual.size
            else samples
        )

        out: list[Segment] = []
        while self._residual.size >= VAD_WINDOW_SAMPLES:
            window = self._residual[:VAD_WINDOW_SAMPLES]
            self._residual = self._residual[VAD_WINDOW_SAMPLES:]
            seg = self._process_window(window, model)
            if seg is not None:
                out.append(seg)
        return out

    # ------------------------------------------------------------------
    def _process_window(
        self, window: np.ndarray, model: StreamingVADModel
    ) -> Segment | None:
        win_start = self._processed
        win_end = win_start + VAD_WINDOW_SAMPLES
        self._processed = win_end
        prob = model.prob(window)

        if not self._triggered:
            # 未触发时维护前置缓冲，避免吞掉首音（speech_pad_ms 的流式等价）
            self._preroll = (
                np.concatenate([self._preroll, window])
                if self._preroll.size
                else window.copy()
            )
            if self._preroll.size > self.pre_roll_samples:
                self._preroll = self._preroll[-self.pre_roll_samples :]
            if prob >= self.threshold:
                self._triggered = True
                self._buf = [self._preroll.copy()]
                # 语音从首窗口即开始（无声前导）时前溯会越界，钳到 0
                self._seg_start = max(0, win_start - self._preroll.size)
                self._last_speech_end = win_end
                self._silence_run = 0
                self._silence_start = None
                self._preroll = np.zeros(0, dtype=np.float32)
            return None

        # ---- 已触发：累积本句音频并跟踪语音/静音 ----
        self._buf.append(window)
        if prob >= self.threshold:
            self._silence_run = 0
            self._silence_start = None
            self._last_speech_end = win_end
        elif prob < self.neg_threshold:
            if self._silence_run == 0:
                self._silence_start = win_start
            self._silence_run += VAD_WINDOW_SAMPLES
        # 过渡区（neg_threshold ≤ prob < threshold）保持现状，不计入静音

        # 句长硬上限（FR-05：句长达到 15s 强制切分）
        if win_end - self._seg_start >= self.max_sentence_samples:
            cut = self._max_cut_point(win_end)
            return self._finalize(cut, continue_speech=True)

        # 静音达到阈值：在最近一次语音末端切句
        if self._silence_run >= self.min_silence_samples:
            return self._finalize(self._last_speech_end, continue_speech=False)

        return None

    def _max_cut_point(self, win_end: int) -> int:
        """句长上限处的切分点：优先切在最近一段足够长的静音处。"""
        if (
            self._silence_start is not None
            and win_end - self._silence_start >= self._min_silence_at_max
        ):
            return self._silence_start
        return win_end

    @staticmethod
    def _concat(buf: list[np.ndarray]) -> np.ndarray:
        if not buf:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(buf)

    def _finalize(self, cut_sample: int, *, continue_speech: bool) -> Segment | None:
        buf = self._concat(self._buf)
        take = max(0, min(buf.size, cut_sample - self._seg_start))
        audio = buf[:take]
        seg: Segment | None = None
        if audio.size >= self.min_speech_samples:
            seg = Segment(
                pcm=audio,
                start_ms=self._ms(self._seg_start),
                end_ms=self._ms(self._seg_start + audio.size),
                seq_start=self._seq_at(self._seg_start),
                seq_end=self._seq_at(self._seg_start + audio.size - 1),
            )
        else:
            log.debug(
                "丢弃过短语音片段：%d ms（阈值 %d ms）",
                self._ms(audio.size),
                self._ms(self.min_speech_samples),
            )

        if continue_speech:
            # 强制切分后语音仍在继续：剩余部分作为新句的开头
            self._buf = [buf[take:]] if buf.size > take else []
            self._seg_start = self._seg_start + take
            self._silence_run = 0
            self._silence_start = None
            self._triggered = bool(self._buf)
        else:
            self._buf = []
            self._triggered = False
            self._silence_run = 0
            self._silence_start = None
            self._preroll = np.zeros(0, dtype=np.float32)
        return seg

    def flush(self) -> Segment | None:
        """冲刷当前句（收到 ``stop`` 时调用，FR-09）。"""
        if not self._triggered or not self._buf:
            self.reset()
            return None
        buf = self._concat(self._buf)
        end = self._last_speech_end - self._seg_start
        if end <= 0 or end > buf.size:
            end = buf.size
        audio = buf[:end]
        seg: Segment | None = None
        if audio.size >= self.min_speech_samples:
            seg = Segment(
                pcm=audio,
                start_ms=self._ms(self._seg_start),
                end_ms=self._ms(self._seg_start + audio.size),
                seq_start=self._seq_at(self._seg_start),
                seq_end=self._seq_at(self._seg_start + audio.size - 1),
            )
        self.reset()
        return seg
