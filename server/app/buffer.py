"""音频缓冲：帧校验、序号连续性检查与丢帧统计（FR-03、FM-07）。

只做"接收侧"的记账工作，不做断句——断句见 :mod:`app.segmenter`。
"""

from __future__ import annotations

from .protocol import INVALID_FRAME_LIMIT, AudioFrame, ProtocolError, decode_audio_frame


class AudioBuffer:
    """按会话维护的音频帧接收统计。"""

    def __init__(self, invalid_limit: int = INVALID_FRAME_LIMIT) -> None:
        self.invalid_limit = invalid_limit
        self.reset()

    def reset(self) -> None:
        self.received = 0
        self.invalid = 0
        self.lost = 0
        self.consecutive_invalid = 0
        self.last_seq: int | None = None
        self._first_seq: int | None = None

    def accept(self, data: bytes) -> AudioFrame:
        """校验并解码一帧。

        非法帧抛出 :class:`ProtocolError`（码 2001），并计入 ``invalid``；
        调用方据此决定是否回 error（连续 ``invalid_limit`` 帧）。
        """
        try:
            frame = decode_audio_frame(data)
        except ProtocolError:
            self.invalid += 1
            self.consecutive_invalid += 1
            raise
        self.consecutive_invalid = 0
        self.received += 1
        if self._first_seq is None:
            self._first_seq = frame.seq
        if self.last_seq is not None:
            gap = frame.seq - self.last_seq
            if gap > 1:
                self.lost += gap - 1
        self.last_seq = frame.seq
        return frame

    @property
    def total(self) -> int:
        return self.received + self.invalid

    @property
    def invalid_ratio(self) -> float:
        return self.invalid / self.total if self.total else 0.0

    @property
    def should_report_bad_quality(self) -> bool:
        """丢帧/非法帧比例是否超过 5%（FM-07 提示阈值）。"""
        if not self.total:
            return False
        return (self.invalid + self.lost) / self.total > 0.05

    def stats(self) -> dict[str, int | float]:
        return {
            "received": self.received,
            "invalid": self.invalid,
            "lost": self.lost,
            "invalid_ratio": round(self.invalid_ratio, 4),
        }
