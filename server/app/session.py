"""会话状态与端到端处理管道（02 文档 §4.3、§4.4）。

每个 WebSocket 连接对应一个 :class:`Session`。管道为：

    音频帧 → 缓冲/丢帧统计 → VAD 断句 → ASR 队列（串行） → 翻译队列（并发受限） → subtitle 回传

故障处理：
* FM-09 队列积压超上限 → 丢最旧句（error 5001）；
* FM-08 ASR 空结果/异常 → 丢弃该句，连续 5 句失败按 FM-04 停止识别；
* FM-02/03 翻译失败 → 降级回传原文并标记，回 error。
"""

from __future__ import annotations

import asyncio
import bisect
from collections import deque
from typing import Awaitable, Callable

import numpy as np

from . import protocol
from .asr import ASREngine, ModelUnavailableError
from .buffer import AudioBuffer
from .protocol import ProtocolError
from .segmenter import Segment, Segmenter
from .settings import Settings
from .telemetry import LatencyStats, SentenceRecord, get_logger, now_ms
from .translate import TranslationService

log = get_logger("session")

SendFn = Callable[[dict], Awaitable[None]]

# 连续 ASR 失败达到此数量即停止识别（FM-08 → FM-04）
ASR_FAILURE_LIMIT = 5


class Session:
    """单个客户端的会话与处理管道。"""

    def __init__(
        self,
        session_id: str,
        settings: Settings,
        asr: ASREngine,
        translation: TranslationService,
        stats: LatencyStats | None = None,
    ) -> None:
        self.session_id = session_id
        self.settings = settings
        self.asr = asr
        self.translation = translation

        self.config: dict | None = None
        self.buffer = AudioBuffer()
        self.segmenter = Segmenter.from_settings(settings)
        # 传入共享的统计对象时，跨会话累计（会话过期后仍保留，供 /status 与 NFR-01 报告使用）
        self.stats = stats if stats is not None else LatencyStats()

        self.sentence_id = 0
        self.paused = True  # 收到 config 且 start 后才会处理音频
        self.blocked = False  # ASR 连续失败后置位
        self._audio_origin_ms: int | None = None
        self._consecutive_asr_fail = 0
        self._last_lost = 0  # 已计入共享统计的丢帧数（避免 buffer 重置导致重复计数）
        # 音频位置 → 该帧到达墙钟时刻的有序表，用于按真实到达时间计算端到端延迟
        # （NFR-01 判据：句末 → 字幕上屏）。直接用「首帧时间 + 标称音频位置」会被
        # 客户端的发送漂移污染，故记录每帧实际到达时刻。
        self._recv_marks: list[tuple[int, int]] = []  # (音频结束毫秒, 到达墙钟毫秒)
        self._recv_ms: int = 0  # 已接收音频的标称时长
        # 队列元素为 (句子, 入队时间戳)；None 为关闭信号
        self._queue: asyncio.Queue[tuple[Segment, int] | None] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._send: SendFn | None = None
        self.last_active_ms = now_ms()
        self.log = log

    # ---- 生命周期 -----------------------------------------------------
    def attach(self, send: SendFn) -> None:
        """绑定（或重绑）回传通道；重连时复用。"""
        self._send = send
        self.last_active_ms = now_ms()

    def detach(self) -> None:
        self._send = None

    async def start_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker())

    async def close(self) -> None:
        if self._worker is not None:
            await self._queue.put(None)
            try:
                await asyncio.wait_for(self._worker, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._worker.cancel()
            self._worker = None
        self.detach()

    def touch(self) -> None:
        self.last_active_ms = now_ms()

    def _wall_for_audio_ms(self, audio_ms: int) -> int | None:
        """返回音频位置 ``audio_ms`` 对应帧的到达墙钟时刻。"""
        if not self._recv_marks:
            return None
        keys = [m[0] for m in self._recv_marks]
        idx = bisect.bisect_left(keys, audio_ms)
        idx = max(0, min(idx, len(self._recv_marks) - 1))
        return self._recv_marks[idx][1]

    # ---- 配置 ---------------------------------------------------------
    def reset_audio_clock(self) -> None:
        """重置会话内音频时间轴。

        与 segmenter.reset() 配套使用：重连、切换配置或 reset 后，句子的
        start_ms/end_ms 会从 0 重新计算，接收帧的到达时刻表也必须同步清空，
        否则延迟查表会命中旧帧，算出几十秒甚至几分钟的假延迟。
        """
        self._recv_ms = 0
        self._recv_marks.clear()
        self._audio_origin_ms = None
        self._last_lost = 0

    def apply_config(self, cfg: dict) -> None:
        self.config = cfg
        # 变更配置后重置管道，避免旧半句混入新会话
        self.segmenter.reset()
        self.buffer.reset()
        self.reset_audio_clock()
        self.paused = True

    # ---- 音频 ---------------------------------------------------------
    def feed_frame(self, data: bytes) -> None:
        """接收一条二进制音频帧。"""
        self.touch()
        if self.blocked:
            return
        # 03 §2：未收到 config 或未 start 时不处理音频
        if self.config is None or self.paused:
            return
        try:
            frame = self.buffer.accept(data)  # 非法帧抛 ProtocolError，由接入层处理
        except ProtocolError:
            self.stats.invalid_frames += 1
            raise
        delta_lost = self.buffer.lost - self._last_lost
        if delta_lost > 0:
            self.stats.lost_frames += delta_lost
        self._last_lost = self.buffer.lost
        self._recv_ms += protocol.CHUNK_MS
        self._recv_marks.append((self._recv_ms, now_ms()))
        if self._audio_origin_ms is None:
            self._audio_origin_ms = now_ms()
        for seg in self.segmenter.feed(frame.pcm, frame.seq):
            self._enqueue(seg)

    def _enqueue(self, seg: Segment) -> None:
        # FM-09：队列积压超上限 → 丢最旧句
        if self._queue.qsize() >= self.settings.max_queue:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self.stats.queue_drops += 1
                self._fire_and_forget(
                    self._emit_error(
                        protocol.ERR_SERVER_BUSY, "处理不及，已跳过部分语句"
                    )
                )
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait((seg, now_ms()))

    # ---- 会话控制 -----------------------------------------------------
    def set_started(self, started: bool) -> None:
        self.touch()
        self.paused = not started
        if started:
            self._audio_origin_ms = self._audio_origin_ms or now_ms()

    async def flush(self) -> None:
        """冲刷当前句（stop，FR-09）。"""
        self.touch()
        seg = self.segmenter.flush()
        if seg is not None:
            self._enqueue(seg)
        self.paused = True

    def reset_pipeline(self) -> None:
        """丢弃未断句音频（reset，FR-09）。"""
        self.touch()
        self.segmenter.reset()
        self.reset_audio_clock()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

    # ---- 管道 ---------------------------------------------------------
    def _record_failure(
        self, sid: int, seg: Segment, queue_wait: int, asr_ms: int
    ) -> None:
        """ASR 失败也保留一条记录，使 /latency 的 sentence_id 连续（不计入 P95）。"""
        self.stats.add(
            SentenceRecord(
                sentence_id=sid,
                audio_ms=seg.duration_ms,
                queue_wait_ms=queue_wait,
                asr_ms=max(0, asr_ms),
                translate_ms=0,
                total_ms=0,
                failed=True,
                session_id=self.session_id,
            )
        )

    async def _run_worker(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                seg, enqueued_ms = item
                await self._handle(seg, enqueued_ms)
            except Exception as exc:  # noqa: BLE001 - 单句异常不得中断会话
                log.exception("处理句子时发生未预期异常：%s", exc)
                self.stats.asr_failures += 1
            finally:
                self._queue.task_done()

    async def _handle(self, seg: Segment, enqueued_ms: int) -> None:
        if self.config is None:
            return
        src = self.config["src_lang"]
        tgt = self.config["tgt_lang"]
        self.sentence_id += 1
        sid = self.sentence_id
        queue_wait = max(0, now_ms() - enqueued_ms)

        # ---- ASR ----
        asr_t0 = now_ms()
        try:
            source = await self.asr.transcribe_async(seg.pcm, src)
            asr_ms = now_ms() - asr_t0
            self._consecutive_asr_fail = 0
        except ModelUnavailableError as exc:
            log.error("模型不可用，停止接受音频（FM-05）：%s", exc)
            self._record_failure(sid, seg, queue_wait, now_ms() - asr_t0)
            await self._emit_error(protocol.ERR_ASR_FAILED, "识别引擎不可用")
            self.blocked = True
            return
        except Exception as exc:  # noqa: BLE001
            self._consecutive_asr_fail += 1
            self.stats.asr_failures += 1
            self._record_failure(sid, seg, queue_wait, now_ms() - asr_t0)
            log.warning(
                "ASR 失败（第 %d/%d 次）：%s",
                self._consecutive_asr_fail,
                ASR_FAILURE_LIMIT,
                exc,
            )
            if self._consecutive_asr_fail >= ASR_FAILURE_LIMIT:
                # FM-08 → FM-04：连续失败，停止识别并保留服务在线
                log.error("连续 %d 句识别失败，停止识别（FM-04/FM-08）", ASR_FAILURE_LIMIT)
                await self._emit_error(protocol.ERR_ASR_FAILED, "识别连续失败，已停止")
                self.blocked = True
            else:
                await self._emit_error(protocol.ERR_ASR_FAILED, "本句识别失败，已跳过")
            return

        if not source:
            # FM-08：空结果静默丢弃；仍保留一条记录，使 /latency 的 sentence_id 连续可对照
            self.stats.add(
                SentenceRecord(
                    sentence_id=sid,
                    audio_ms=seg.duration_ms,
                    queue_wait_ms=queue_wait,
                    asr_ms=asr_ms,
                    translate_ms=0,
                    total_ms=0,
                    empty=True,
                    session_id=self.session_id,
                )
            )
            log.debug("句 %d 识别为空，丢弃", sid)
            return

        # ---- 翻译 ----
        outcome = await self.translation.translate(source, src, tgt)
        if outcome.degraded:
            self.stats.translate_failures += 1
        created_ms = now_ms()
        end_wall = self._wall_for_audio_ms(seg.end_ms)
        total_ms = max(0, created_ms - end_wall) if end_wall is not None else 0

        record = SentenceRecord(
            sentence_id=sid,
            audio_ms=seg.duration_ms,
            queue_wait_ms=queue_wait,
            asr_ms=asr_ms,
            translate_ms=outcome.elapsed_ms,
            total_ms=total_ms,
            source_chars=len(source),
            translation_chars=len(outcome.text),
            degraded=outcome.degraded,
            session_id=self.session_id,
        )
        self.stats.add(record)

        await self._send_msg(
            protocol.subtitle(
                sentence_id=sid,
                seq_start=seg.seq_start,
                seq_end=seg.seq_end,
                source=source,
                translation=outcome.text,
                src_lang=src,
                tgt_lang=tgt,
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                created_ms=created_ms,
                asr_ms=asr_ms,
                translate_ms=outcome.elapsed_ms,
                translation_degraded=outcome.degraded,
            )
        )
        if outcome.degraded and outcome.error_code is not None:
            await self._emit_error(
                outcome.error_code,
                "本句翻译失败，已回传原文",
            )
        log.info(
            "字幕 #%d  %dms 句  ASR %dms  翻译 %dms  总延迟 %dms  %s",
            sid,
            seg.duration_ms,
            asr_ms,
            outcome.elapsed_ms,
            total_ms,
            "（降级）" if outcome.degraded else "",
        )
        # FR-21 成本通知（若有）
        for note in self.translation.drain_notifications():
            await self._send_msg(protocol.status("cost_notice", note))

    # ---- 回传 ---------------------------------------------------------
    async def _send_msg(self, msg: dict) -> None:
        if self._send is None:
            return
        try:
            await self._send(msg)
        except Exception as exc:  # noqa: BLE001 - 回传失败不回滚处理
            log.warning("回传失败：%s", exc)

    async def _emit_error(self, code: int, message: str = "") -> None:
        await self._send_msg(protocol.error(code, message))

    def _fire_and_forget(self, coro: Awaitable[None]) -> None:
        """在事件循环中调度一个不等待的小任务（用于积压时的即时提示）。"""
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            pass
