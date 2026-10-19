"""会话音频时钟测试：重连/重置后延迟查表不能命中旧帧。"""

from __future__ import annotations

import numpy as np

from app.protocol import CHUNK_SAMPLES, encode_audio_frame


class _Stub:
    """本测试不触发 ASR/翻译，仅验证时间轴。"""


def test_audio_clock_resets_with_config(settings):
    from app.session import Session

    session = Session("s-clock", settings, _Stub(), _Stub())
    cfg = {
        "mode": "mic",
        "src_lang": "en",
        "tgt_lang": "zh",
        "sample_rate": 16000,
        "chunk_ms": 100,
    }
    session.apply_config(cfg)
    session.set_started(True)
    frame = encode_audio_frame(1, np.zeros(CHUNK_SAMPLES, dtype=np.int16))

    for _ in range(5):
        session.feed_frame(frame)
    assert session._recv_ms == 500
    assert len(session._recv_marks) == 5

    # 重连/重新配置：音频时间轴必须回到 0，否则句末会映射到旧帧
    session.apply_config(cfg)
    assert session._recv_ms == 0
    assert session._recv_marks == []
    assert session._audio_origin_ms is None

    session.set_started(True)
    session.feed_frame(frame)
    assert session._recv_ms == 100
    assert len(session._recv_marks) == 1
    wall = session._wall_for_audio_ms(100)
    assert wall is not None


def test_reset_pipeline_also_resets_clock(settings):
    from app.session import Session

    session = Session("s-reset", settings, _Stub(), _Stub())
    session.apply_config(
        {
            "mode": "mic",
            "src_lang": "en",
            "tgt_lang": "zh",
            "sample_rate": 16000,
            "chunk_ms": 100,
        }
    )
    session.set_started(True)
    frame = encode_audio_frame(1, np.zeros(CHUNK_SAMPLES, dtype=np.int16))
    for _ in range(3):
        session.feed_frame(frame)
    assert session._recv_ms == 300

    session.reset_pipeline()
    assert session._recv_ms == 0
    assert session._recv_marks == []
