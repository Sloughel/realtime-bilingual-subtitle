"""VAD 断句器单元测试（任务 1.3）。

验收判据（04 文档 §2 M1）：离线音频切句边界与人工标注误差 ≤ ±300ms。
"""

from __future__ import annotations

import numpy as np
import pytest

from app.protocol import CHUNK_SAMPLES
from app.segmenter import Segmenter

SR = 16000


def slice_ms(pcm16: np.ndarray, a: float, b: float) -> np.ndarray:
    return pcm16[int(a * SR) : int(b * SR)]


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.int16)


def feed_all(seg: Segmenter, pcm16: np.ndarray) -> list:
    out = []
    seq = 0
    for i in range(0, pcm16.size, CHUNK_SAMPLES):
        chunk = pcm16[i : i + CHUNK_SAMPLES]
        if chunk.size < CHUNK_SAMPLES:
            chunk = np.concatenate(
                [chunk, np.zeros(CHUNK_SAMPLES - chunk.size, dtype=np.int16)]
            )
        seq += 1
        out.extend(seg.feed(chunk, seq))
    return out


@pytest.fixture(scope="module")
def pcm(fixture_pcm16) -> np.ndarray:
    return fixture_pcm16


def test_two_sentences_boundaries_within_300ms(pcm):
    """已知时间轴：语音段 1.5–3.9s 与 5.5–8.4s（原音频中的真实语音区间）。"""
    timeline = np.concatenate(
        [
            silence(0.5),
            slice_ms(pcm, 1.5, 3.9),  # 落在 0.5–2.9s
            silence(1.2),
            slice_ms(pcm, 5.5, 8.4),  # 落在 4.1–7.0s
            silence(0.8),
        ]
    )
    expected = [(0.5, 2.9), (4.1, 7.0)]

    seg = Segmenter()
    segments = feed_all(seg, timeline)
    tail = seg.flush()
    if tail is not None:
        segments.append(tail)

    assert len(segments) == len(expected), [s.duration_ms for s in segments]
    for (exp_s, exp_e), s in zip(expected, segments):
        assert abs(s.start_ms / 1000 - exp_s) <= 0.3, (s.start_ms / 1000, exp_s)
        assert abs(s.end_ms / 1000 - exp_e) <= 0.3, (s.end_ms / 1000, exp_e)


def test_silence_only_produces_no_segment():
    seg = Segmenter()
    segments = feed_all(seg, silence(2.0))
    assert segments == []
    assert seg.flush() is None


def test_flush_emits_trailing_sentence(pcm):
    """无尾部静音时，stop 冲刷应产出整句（FR-09）。"""
    seg = Segmenter()
    # 仅语音，不追加静音 → 静音未达阈值，feed 不产出
    segments = feed_all(seg, slice_ms(pcm, 1.5, 4.0))
    assert segments == []
    tail = seg.flush()
    assert tail is not None
    assert tail.duration_ms >= 1500


def test_reset_discards_pending(pcm):
    seg = Segmenter()
    feed_all(seg, slice_ms(pcm, 1.5, 3.0))
    seg.reset()
    assert seg.flush() is None


def test_max_sentence_forces_split(pcm):
    """句长上限 15s 的机制：用 1s 上限验证强制切分。"""
    seg = Segmenter(max_sentence_ms=1000, pre_roll_ms=0)
    segments = feed_all(seg, slice_ms(pcm, 1.5, 4.1))  # 2.6s 连续语音
    tail = seg.flush()
    if tail is not None:
        segments.append(tail)
    assert len(segments) >= 2, [s.duration_ms for s in segments]
    for s in segments:
        assert s.duration_ms <= 1200  # 允许一个窗口的余量


def test_segment_metadata_monotonic(pcm):
    seg = Segmenter()
    segments = feed_all(seg, np.concatenate([slice_ms(pcm, 1.5, 3.9), silence(1.2), slice_ms(pcm, 5.5, 8.4), silence(1.0)]))
    assert segments
    prev_end = -1
    for s in segments:
        assert s.start_ms < s.end_ms
        assert s.start_ms > prev_end
        assert s.seq_end >= s.seq_start >= 1
        assert s.pcm.dtype == np.float32
        assert abs(s.pcm).max() <= 1.0
        prev_end = s.end_ms
