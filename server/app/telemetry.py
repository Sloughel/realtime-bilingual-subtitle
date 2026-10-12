"""日志与各阶段耗时统计（02 文档 §4.2 telemetry、§11 可观测性）。

支撑 NFR-01 的延迟验收：逐句记录断句/识别/翻译/总延迟，输出分位统计。
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


def now_ms() -> int:
    return int(time.time() * 1000)


def setup_logging(level: str = "INFO") -> None:
    """配置根日志（幂等）。"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@dataclass
class SentenceRecord:
    """单句的耗时与结果记录，供延迟报告使用。"""

    sentence_id: int
    audio_ms: int  # 该句音频时长
    queue_wait_ms: int  # 进入队列到开始 ASR 的等待
    asr_ms: int
    translate_ms: int
    total_ms: int  # 句末 → 字幕生成（字幕上屏的代理指标）
    source_chars: int = 0
    translation_chars: int = 0
    degraded: bool = False
    failed: bool = False


@dataclass
class LatencyStats:
    """会话级延迟统计。"""

    records: list[SentenceRecord] = field(default_factory=list)
    invalid_frames: int = 0
    lost_frames: int = 0
    asr_failures: int = 0
    translate_failures: int = 0
    queue_drops: int = 0

    def add(self, record: SentenceRecord) -> None:
        self.records.append(record)

    # -- 分位统计 ---------------------------------------------------------
    @staticmethod
    def _pct(values: list[float], p: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        if len(ordered) == 1:
            return ordered[0]
        k = (len(ordered) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)

    def summary(self) -> dict[str, float | int]:
        totals = [r.total_ms for r in self.records]
        return {
            "sentences": len(self.records),
            "total_ms_avg": round(statistics.fmean(totals), 1) if totals else 0.0,
            "total_ms_p50": round(self._pct(totals, 0.50), 1),
            "total_ms_p95": round(self._pct(totals, 0.95), 1),
            "total_ms_max": round(max(totals), 1) if totals else 0.0,
            "asr_ms_p95": round(self._pct([r.asr_ms for r in self.records], 0.95), 1),
            "translate_ms_p95": round(
                self._pct([r.translate_ms for r in self.records], 0.95), 1
            ),
            "degraded": sum(1 for r in self.records if r.degraded),
            "failed": sum(1 for r in self.records if r.failed),
            "invalid_frames": self.invalid_frames,
            "lost_frames": self.lost_frames,
            "asr_failures": self.asr_failures,
            "translate_failures": self.translate_failures,
            "queue_drops": self.queue_drops,
        }

    def to_json(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "summary": self.summary(),
            "records": [asdict(r) for r in self.records],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
