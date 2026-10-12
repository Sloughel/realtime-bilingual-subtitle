"""WebSocket 接入：握手、消息分发、会话注册与重连（02 文档 §4.4、03 文档 §2/§4/§5/§7）。"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from . import protocol
from .asr import ASREngine
from .protocol import (
    INVALID_FRAME_LIMIT,
    PROTOCOL_VERSION,
    ProtocolError,
    dumps,
    parse_config,
    parse_control_message,
)
from .session import Session
from .settings import Settings
from .telemetry import get_logger, now_ms
from .translate import TranslationService

log = get_logger("connection")


class WsSender:
    """串行化的发送通道。

    ASR 工作线程与主收发循环可能同时写同一连接，Starlette 不允许并发发送，
    故用锁串行化。
    """

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self._lock = asyncio.Lock()

    async def send_json(self, msg: dict) -> None:
        async with self._lock:
            await self.websocket.send_text(dumps(msg))


class SessionRegistry:
    """会话注册表：按 session_id 复用（重连恢复），按 TTL 回收（FM-01）。"""

    def __init__(
        self, settings: Settings, asr: ASREngine, translation: TranslationService
    ) -> None:
        self.settings = settings
        self.asr = asr
        self.translation = translation
        self._sessions: dict[str, Session] = {}
        self._detached_since: dict[str, int] = {}

    # ------------------------------------------------------------------
    def get_or_create(self, session_id: str) -> tuple[Session, bool]:
        """返回 (会话, 是否为重连复用)。"""
        session = self._sessions.get(session_id)
        if session is not None and session_id in self._detached_since:
            # 重连：复用状态，丢弃半句（02 §4.4）
            session.segmenter.reset()
            self._detached_since.pop(session_id, None)
            log.info("会话重连复用：%s（sentence_id 续至 %d）", session_id, session.sentence_id)
            return session, True
        if session is None:
            session = Session(session_id, self.settings, self.asr, self.translation)
            self._sessions[session_id] = session
            log.info("新建会话：%s", session_id)
            return session, False
        return session, False

    def mark_detached(self, session_id: str) -> None:
        self._detached_since[session_id] = now_ms()

    def active_count(self) -> int:
        return len(self._sessions)

    def sessions(self) -> list[Session]:
        return list(self._sessions.values())

    # ------------------------------------------------------------------
    async def reap_loop(self) -> None:
        """按 SESSION_TTL_S 回收已断开会话。"""
        try:
            while True:
                await asyncio.sleep(5)
                ttl_ms = self.settings.session_ttl_s * 1000
                now = now_ms()
                for sid in list(self._detached_since):
                    if now - self._detached_since[sid] >= ttl_ms:
                        session = self._sessions.pop(sid, None)
                        self._detached_since.pop(sid, None)
                        if session is not None:
                            await session.close()
                            log.info("会话已回收（超时 %ds）：%s", self.settings.session_ttl_s, sid)
        except asyncio.CancelledError:
            pass

    async def close_all(self) -> None:
        for session in list(self._sessions.values()):
            await session.close()
        self._sessions.clear()
        self._detached_since.clear()


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


async def handle_websocket(websocket: WebSocket) -> None:
    state = websocket.app.state
    settings: Settings = state.settings
    registry: SessionRegistry = state.registry

    params = websocket.query_params
    raw_pv = params.get("protocol_version")
    session_id = params.get("session_id") or str(uuid.uuid4())
    await websocket.accept()

    # 协议版本校验（03 §2）
    if raw_pv is not None and raw_pv != str(PROTOCOL_VERSION):
        await websocket.send_text(
            dumps(protocol.error(protocol.ERR_BAD_PROTOCOL, f"仅支持协议版本 {PROTOCOL_VERSION}"))
        )
        await websocket.close(code=1008)
        return

    session, reconnected = registry.get_or_create(session_id)
    sender = WsSender(websocket)
    session.attach(sender.send_json)
    await session.start_worker()

    # 握手应答
    await sender.send_json(
        protocol.hello(now_ms(), max_sentence_ms=settings.vad_max_sentence_ms)
    )
    if registry.asr is not None and getattr(registry.asr, "degraded", False):
        await sender.send_json(
            protocol.status(
                "asr_degraded",
                "识别已在 CPU 上运行，延迟可能偏高（GPU 不可用）",
            )
        )
    if not settings.translate_configured:
        await sender.send_json(
            protocol.status("translate_unavailable", "服务端未配置翻译密钥，字幕仅含原文")
        )

    try:
        while True:
            message = await websocket.receive()
            mtype = message.get("type")
            if mtype == "websocket.disconnect":
                break
            data = message.get("bytes")
            text = message.get("text")
            if data is not None:
                if not await _on_audio(session, sender, data):
                    break
            elif text is not None:
                await _on_control(session, sender, text)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.exception("连接循环异常：%s", exc)
    finally:
        session.detach()
        registry.mark_detached(session_id)
        log.info(
            "连接断开：%s（%s）", session_id, "重连会话" if reconnected else "普通会话"
        )


async def _on_audio(session: Session, sender: WsSender, data: bytes) -> bool:
    """处理一条音频帧。返回 False 表示应断开连接。"""
    try:
        session.feed_frame(data)
    except ProtocolError as exc:
        if session.buffer.consecutive_invalid >= INVALID_FRAME_LIMIT:
            # FM-07：连续非法帧达上限 → 回 error 并断开该会话
            await sender.send_json(
                protocol.error(
                    protocol.ERR_BAD_FRAME,
                    f"连续 {INVALID_FRAME_LIMIT} 帧非法：{exc.message}",
                )
            )
            log.warning("会话 %s 连续非法帧超限，断开", session.session_id)
            return False
    return True


async def _on_control(session: Session, sender: WsSender, text: str) -> None:
    try:
        msg = parse_control_message(text)
    except ProtocolError as exc:
        await sender.send_json(protocol.error(exc.code, exc.message))
        return

    mtype = msg.get("type")
    settings: Settings = session.settings

    if mtype == "config":
        try:
            cfg = parse_config(msg)
        except ProtocolError as exc:
            await sender.send_json(protocol.error(exc.code, exc.message))
            return
        session.apply_config(cfg)
        warnings: list[str] = []
        if not settings.translate_configured:
            warnings.append("翻译后端未配置，字幕将只显示原文")
        if getattr(session.asr, "degraded", False):
            warnings.append("识别运行在 CPU 上，延迟可能偏高")
        await sender.send_json(
            protocol.config_ack(
                session.session_id,
                True,
                {"src_lang": cfg["src_lang"], "tgt_lang": cfg["tgt_lang"], "mode": cfg["mode"]},
                warnings,
            )
        )
        log.info(
            "会话 %s 配置：%s→%s，mode=%s",
            session.session_id,
            cfg["src_lang"],
            cfg["tgt_lang"],
            cfg["mode"],
        )
    elif mtype == "start":
        session.set_started(True)
    elif mtype == "stop":
        await session.flush()
    elif mtype == "reset":
        session.reset_pipeline()
    elif mtype == "ping":
        await sender.send_json(protocol.pong(int(msg.get("t", now_ms()))))
    else:
        await sender.send_json(
            protocol.error(protocol.ERR_BAD_PARAM, f"未知消息类型：{mtype!r}")
        )
