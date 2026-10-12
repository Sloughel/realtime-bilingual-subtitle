"""音频缓冲与丢帧统计的单元测试（任务 1.3 的接收侧、FM-07）。"""

from __future__ import annotations

import numpy as np
import pytest

from app.buffer import AudioBuffer
from app.protocol import CHUNK_SAMPLES, ProtocolError, encode_audio_frame


def frame(seq: int) -> bytes:
    return encode_audio_frame(seq, np.zeros(CHUNK_SAMPLES, dtype=np.int16))


def test_accept_counts_and_seq_tracking():
    buf = AudioBuffer()
    for seq in range(1, 6):
        buf.accept(frame(seq))
    assert buf.received == 5
    assert buf.invalid == 0
    assert buf.lost == 0
    assert buf.last_seq == 5
    assert buf.total == 5


def test_lost_frames_detected_on_seq_gap():
    buf = AudioBuffer()
    buf.accept(frame(1))
    buf.accept(frame(2))
    buf.accept(frame(7))  # 丢了 3,4,5,6
    assert buf.lost == 4
    assert buf.received == 3


def test_invalid_frame_raises_and_counts():
    buf = AudioBuffer()
    with pytest.raises(ProtocolError) as exc:
        buf.accept(b"\x00\x01\x02")
    assert exc.value.code == 2001
    assert buf.invalid == 1
    assert buf.consecutive_invalid == 1


def test_consecutive_invalid_resets_on_valid_frame():
    buf = AudioBuffer()
    for _ in range(3):
        with pytest.raises(ProtocolError):
            buf.accept(b"bad")
    assert buf.consecutive_invalid == 3
    buf.accept(frame(1))
    assert buf.consecutive_invalid == 0


def test_bad_quality_flag_over_five_percent():
    buf = AudioBuffer()
    for seq in range(1, 20):
        buf.accept(frame(seq))
    for _ in range(2):
        with pytest.raises(ProtocolError):
            buf.accept(b"bad")
    # 2 / 21 ≈ 9.5% > 5%
    assert buf.should_report_bad_quality is True


def test_reset_clears_stats():
    buf = AudioBuffer()
    buf.accept(frame(1))
    buf.reset()
    assert buf.received == 0 and buf.last_seq is None
