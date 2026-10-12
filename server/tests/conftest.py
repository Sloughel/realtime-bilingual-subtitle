"""pytest 公共夹具。

运行：``cd server && ../.venv/Scripts/python.exe -m pytest -q``
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

FIXTURE_WAV = Path(__file__).resolve().parent / "fixtures" / "sample_en.wav"

from app.protocol import CHUNK_SAMPLES  # noqa: E402


@pytest.fixture
def settings():
    """不读取真实 .env 的测试配置（密钥为假值，避免任何真实网络调用）。"""
    from app.settings import Settings

    return Settings(
        _env_file=None,
        translate_api_key="sk-test-not-real",
        whisper_model="medium",
        translate_max_retry=0,
        session_ttl_s=1,
    )


@pytest.fixture(scope="session")
def fixture_pcm16() -> np.ndarray:
    """测试音频（16kHz 单声道），按需重采样。"""
    from faster_whisper.audio import decode_audio

    audio = decode_audio(str(FIXTURE_WAV), sampling_rate=16000).astype(np.float32)
    return np.clip(audio * 32767, -32768, 32767).astype(np.int16)


def make_frames(pcm16: np.ndarray):
    """把 PCM16 切成 100ms 协议帧并编号（从 1 起）。"""
    from app.protocol import encode_audio_frame

    frames = []
    seq = 0
    for i in range(0, pcm16.size, CHUNK_SAMPLES):
        chunk = pcm16[i : i + CHUNK_SAMPLES]
        if chunk.size < CHUNK_SAMPLES:
            chunk = np.concatenate(
                [chunk, np.zeros(CHUNK_SAMPLES - chunk.size, dtype=np.int16)]
            )
        seq += 1
        frames.append(encode_audio_frame(seq, chunk))
    return frames
