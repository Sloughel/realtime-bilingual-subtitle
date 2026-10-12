"""协议编解码与消息构造的单元测试（任务 1.2）。"""

from __future__ import annotations

import numpy as np
import pytest

from app import protocol as P


def test_frame_roundtrip():
    pcm = np.arange(P.CHUNK_SAMPLES, dtype=np.int16)
    data = P.encode_audio_frame(7, pcm)
    assert len(data) == P.FRAME_BYTES == 3206
    frame = P.decode_audio_frame(data)
    assert frame.seq == 7
    assert np.array_equal(frame.pcm, pcm)
    assert data[0] == P.MAGIC and data[1] == P.FRAME_VERSION


def test_frame_seq_is_big_endian():
    data = P.encode_audio_frame(0x01020304, np.zeros(P.CHUNK_SAMPLES, np.int16))
    assert data[2:6] == b"\x01\x02\x03\x04"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d[:-1],  # 长度不足
        lambda d: b"\x00" + d[1:],  # 魔数错误
        lambda d: d[:1] + b"\x09" + d[2:],  # 版本错误
        lambda d: d + b"\x00",  # 长度超出
    ],
)
def test_invalid_frames_raise_2001(mutate):
    good = P.encode_audio_frame(1, np.zeros(P.CHUNK_SAMPLES, np.int16))
    with pytest.raises(P.ProtocolError) as exc:
        P.decode_audio_frame(mutate(good))
    assert exc.value.code == P.ERR_BAD_FRAME == 2001


def test_parse_config_ok():
    cfg = P.parse_config({"type": "config", "mode": "media", "src_lang": "en", "tgt_lang": "zh"})
    assert cfg == {
        "mode": "media",
        "src_lang": "en",
        "tgt_lang": "zh",
        "sample_rate": 16000,
        "chunk_ms": 100,
    }


@pytest.mark.parametrize(
    "msg",
    [
        {"type": "config", "mode": "camera"},  # 非法模式
        {"type": "config", "src_lang": "fr"},  # 不支持的源语言
        {"type": "config", "src_lang": "en", "tgt_lang": "en"},  # 同语言
        {"type": "config", "sample_rate": 8000},  # 采样率不符
        {"type": "config", "chunk_ms": 200},  # 分块时长不符
    ],
)
def test_parse_config_rejects(msg):
    with pytest.raises(P.ProtocolError) as exc:
        P.parse_config(msg)
    assert exc.value.code == P.ERR_BAD_PARAM == 1001


def test_parse_control_message_errors():
    with pytest.raises(P.ProtocolError) as e1:
        P.parse_control_message("{not json}")
    assert e1.value.code == 1001
    with pytest.raises(P.ProtocolError) as e2:
        P.parse_control_message('{"foo": 1}')
    assert e2.value.code == 1001


def test_message_shapes():
    hello = P.hello(1757318400000)
    assert hello["type"] == "hello"
    assert hello["protocol_version"] == P.PROTOCOL_VERSION == 1
    assert hello["limits"]["chunk_ms"] == 100
    assert hello["limits"]["sample_rate"] == 16000

    ack = P.config_ack("sid", True, {"src_lang": "en"}, ["w"])
    assert ack["ok"] is True and ack["session_id"] == "sid" and ack["warnings"] == ["w"]

    sub = P.subtitle(
        sentence_id=3,
        seq_start=10,
        seq_end=20,
        source="Hello",
        translation="你好",
        src_lang="en",
        tgt_lang="zh",
        start_ms=1000,
        end_ms=2000,
        created_ms=2100,
        asr_ms=300,
        translate_ms=800,
        translation_degraded=True,
    )
    assert sub["type"] == "subtitle"
    assert sub["translation_degraded"] is True
    assert sub["asr_ms"] == 300 and sub["translate_ms"] == 800

    err = P.error(P.ERR_BAD_PROTOCOL)
    assert err["recoverable"] is False  # 1002 不可恢复
    err2 = P.error(P.ERR_ASR_FAILED)
    assert err2["recoverable"] is True


def test_dumps_is_utf8_readable():
    assert "你好" in P.dumps({"t": "你好"})
    assert "\\u" not in P.dumps({"t": "你好"})
