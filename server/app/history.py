"""字幕历史：把每句字幕追加到本地 JSONL（可选，默认关闭）。

设计要点：

* **旁路功能**：历史写入失败只记日志，不影响字幕回传与处理管道；
* **本地文件**：默认路径 ``logs/subtitles.jsonl``，已被 ``.gitignore`` 排除；
* **隐私**：原文/译文属于用户数据，是否落盘由部署者通过
  ``SUBTITLE_HISTORY`` 决定（默认 false），开启时启动日志会给出提示；
* **并发安全**：同一进程内多个会话可能同时写入，用 ``asyncio.Lock`` 串行化，
  文件写入放到线程池，避免阻塞事件循环。

每行一个 JSON 对象，字段见 :meth:`SubtitleHistory.append` 的调用处
（``session.py``）：时间、会话、句子、语言、原文、译文、各阶段耗时与降级标记。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .telemetry import get_logger

log = get_logger("history")


class SubtitleHistory:
    """JSONL 字幕历史写入器。"""

    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled
        self._lock = asyncio.Lock()
        if self.enabled:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                log.warning("无法创建字幕历史目录 %s：%s", self.path.parent, exc)
                self.enabled = False

    async def append(self, record: dict[str, Any]) -> None:
        """追加一条字幕记录；失败不抛出，避免影响字幕回传。"""
        if not self.enabled:
            return
        row = {
            "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            **record,
        }
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        async with self._lock:
            try:
                await asyncio.to_thread(self._write, line)
            except Exception as exc:  # noqa: BLE001 - 历史是旁路，任何失败都不得影响字幕
                log.warning("写入字幕历史失败（%s）：%s", self.path, exc)

    def _write(self, line: str) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
