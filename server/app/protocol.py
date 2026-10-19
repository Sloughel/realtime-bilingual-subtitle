"""通信协议：常量、音频帧编解码、控制消息构造与校验。

本模块是《03-接口与通信协议设计》的代码映射，字段级定义以该文档为准。
一切与客户端交换的字节都经由此处，便于单元测试（04 文档 §4「单元测试」）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# 常量（03 文档 §1、§3）
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = 1
WS_PATH = "/ws"

MAGIC = 0xAF
FRAME_VERSION = 1

SAMPLE_RATE = 16000
CHUNK_MS = 100
BYTES_PER_SAMPLE = 2
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_MS // 1000  # 1600
CHUNK_BYTES = CHUNK_SAMPLES * BYTES_PER_SAMPLE  # 3200
FRAME_HEADER_BYTES = 6
FRAME_BYTES = FRAME_HEADER_BYTES + CHUNK_BYTES  # 3206

MAX_SENTENCE_MS = 8000
# 连续非法帧达到此数量即回 error(2001)（03 文档 §3、§6）
INVALID_FRAME_LIMIT = 10

# 语言枚举与名称映射（03 文档 §8）；新增语言对只需在此登记
LANG_NAMES: dict[str, str] = {"en": "英语", "zh": "中文", "ja": "日语"}
SUPPORTED_LANGS: tuple[str, ...] = ("zh", "en", "ja")
MODES: tuple[str, ...] = ("mic", "media")

# 错误码（03 文档 §6）
ERR_BAD_PARAM = 1001
ERR_BAD_PROTOCOL = 1002
ERR_BAD_FRAME = 2001
ERR_ASR_FAILED = 3001
ERR_TRANSLATE_FAILED = 4001
ERR_TRANSLATE_TIMEOUT = 4002
ERR_SERVER_BUSY = 5001

ERROR_MESSAGES: dict[int, str] = {
    ERR_BAD_PARAM: "参数非法",
    ERR_BAD_PROTOCOL: "协议版本不支持",
    ERR_BAD_FRAME: "音频帧格式错误",
    ERR_ASR_FAILED: "语音识别失败",
    ERR_TRANSLATE_FAILED: "翻译失败",
    ERR_TRANSLATE_TIMEOUT: "翻译超时",
    ERR_SERVER_BUSY: "服务器繁忙",
}

# 可恢复性：单句错误永不导致连接断开（03 文档 §6 原则）
RECOVERABLE: dict[int, bool] = {
    ERR_BAD_PARAM: True,
    ERR_BAD_PROTOCOL: False,
    ERR_BAD_FRAME: True,
    ERR_ASR_FAILED: True,
    ERR_TRANSLATE_FAILED: True,
    ERR_TRANSLATE_TIMEOUT: True,
    ERR_SERVER_BUSY: True,
}


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class ProtocolError(Exception):
    """携带协议错误码的异常，供接入层转换为 error 消息。"""

    def __init__(self, code: int, message: str = "") -> None:
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code, "协议错误")
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# 音频帧（03 文档 §3）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AudioFrame:
    """一条解码后的音频帧。"""

    seq: int
    pcm: np.ndarray  # int16，长度 CHUNK_SAMPLES


def decode_audio_frame(data: bytes) -> AudioFrame:
    """校验并解码一条二进制音频帧。

    非法帧（魔数错误 / 版本不符 / 长度不符）抛 :class:`ProtocolError`（2001），
    由调用方计数处理（FM-07）。
    """
    if len(data) != FRAME_BYTES:
        raise ProtocolError(
            ERR_BAD_FRAME, f"帧长度应为 {FRAME_BYTES} 字节，实际 {len(data)}"
        )
    if data[0] != MAGIC:
        raise ProtocolError(ERR_BAD_FRAME, f"魔数应为 0x{MAGIC:02X}，实际 0x{data[0]:02X}")
    if data[1] != FRAME_VERSION:
        raise ProtocolError(ERR_BAD_FRAME, f"帧版本应为 {FRAME_VERSION}，实际 {data[1]}")
    seq = int.from_bytes(data[2:6], "big")
    pcm = np.frombuffer(data[FRAME_HEADER_BYTES:], dtype="<i2")
    return AudioFrame(seq=seq, pcm=pcm)


def encode_audio_frame(seq: int, pcm: np.ndarray) -> bytes:
    """把一段 PCM16 打包成协议帧（模拟客户端与测试使用）。"""
    if pcm.dtype != np.int16:
        raise ValueError("pcm 必须为 int16")
    if pcm.size != CHUNK_SAMPLES:
        raise ValueError(f"每帧应为 {CHUNK_SAMPLES} 采样点，实际 {pcm.size}")
    header = bytes((MAGIC, FRAME_VERSION)) + (seq & 0xFFFFFFFF).to_bytes(4, "big")
    return header + pcm.tobytes()


# ---------------------------------------------------------------------------
# 控制消息
# ---------------------------------------------------------------------------


def dumps(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def parse_control_message(text: str) -> dict[str, Any]:
    """解析并粗校验一条 JSON 控制消息。"""
    try:
        msg = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(ERR_BAD_PARAM, f"JSON 解析失败：{exc.msg}") from exc
    if not isinstance(msg, dict) or "type" not in msg:
        raise ProtocolError(ERR_BAD_PARAM, "控制消息必须为含 type 字段的 JSON 对象")
    return msg


def parse_config(msg: dict[str, Any]) -> dict[str, Any]:
    """校验 config 消息并返回规范化后的配置（03 文档 §4.1）。"""
    mode = msg.get("mode", "mic")
    src = msg.get("src_lang", "en")
    tgt = msg.get("tgt_lang", "zh")
    sample_rate = int(msg.get("sample_rate", SAMPLE_RATE))
    chunk_ms = int(msg.get("chunk_ms", CHUNK_MS))

    if mode not in MODES:
        raise ProtocolError(ERR_BAD_PARAM, f"mode 应为 {MODES}，实际 {mode!r}")
    if src not in SUPPORTED_LANGS or tgt not in SUPPORTED_LANGS:
        raise ProtocolError(
            ERR_BAD_PARAM, f"语言取值应为 {SUPPORTED_LANGS}，实际 {src!r}→{tgt!r}"
        )
    if src == tgt:
        raise ProtocolError(ERR_BAD_PARAM, "源语言与目标语言不能相同")
    if sample_rate != SAMPLE_RATE:
        raise ProtocolError(ERR_BAD_PARAM, f"v1.0 仅支持 {SAMPLE_RATE}Hz")
    if chunk_ms != CHUNK_MS:
        raise ProtocolError(ERR_BAD_PARAM, f"v1.0 仅支持 {CHUNK_MS}ms 分块")

    return {
        "mode": mode,
        "src_lang": src,
        "tgt_lang": tgt,
        "sample_rate": sample_rate,
        "chunk_ms": chunk_ms,
    }


# ---------------------------------------------------------------------------
# 服务端消息构造（03 文档 §2、§5）
# ---------------------------------------------------------------------------


def hello(now_ms: int, *, max_sentence_ms: int = MAX_SENTENCE_MS) -> dict[str, Any]:
    return {
        "type": "hello",
        "protocol_version": PROTOCOL_VERSION,
        "server_version": _app_version(),
        "server_time": now_ms,
        "limits": {
            "max_sentence_ms": max_sentence_ms,
            "chunk_ms": CHUNK_MS,
            "sample_rate": SAMPLE_RATE,
        },
    }


def config_ack(
    session_id: str,
    ok: bool,
    applied: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "config_ack",
        "ok": ok,
        "session_id": session_id,
        "applied": applied or {},
        "warnings": warnings or [],
    }


def subtitle(
    *,
    sentence_id: int,
    seq_start: int,
    seq_end: int,
    source: str,
    translation: str,
    src_lang: str,
    tgt_lang: str,
    start_ms: int,
    end_ms: int,
    created_ms: int,
    asr_ms: int,
    translate_ms: int,
    translation_degraded: bool = False,
) -> dict[str, Any]:
    """构造 subtitle 消息（03 文档 §5.2）。"""
    msg: dict[str, Any] = {
        "type": "subtitle",
        "sentence_id": sentence_id,
        "seq_start": seq_start,
        "seq_end": seq_end,
        "source": source,
        "translation": translation,
        "src_lang": src_lang,
        "tgt_lang": tgt_lang,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "created_ms": created_ms,
        "asr_ms": asr_ms,
        "translate_ms": translate_ms,
    }
    if translation_degraded:
        # 降级标记：translation 等于 source（FM-02 / FM-03）
        msg["translation_degraded"] = True
    return msg


def error(code: int, message: str = "", recoverable: bool | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "type": "error",
        "code": code,
        "message": message or ERROR_MESSAGES.get(code, "错误"),
        "recoverable": RECOVERABLE.get(code, True) if recoverable is None else recoverable,
    }
    return msg


def status(code: str, message: str) -> dict[str, Any]:
    return {"type": "status", "code": code, "message": message}


def pong(t: int) -> dict[str, Any]:
    return {"type": "pong", "t": t}


def _app_version() -> str:
    from . import APP_VERSION

    return APP_VERSION
