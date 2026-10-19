"""会话注册表测试：延迟统计需跨会话累计（会话回收后不丢失）。"""

from __future__ import annotations


class _Stub:
    """Session 构造只需要占位对象；本测试不触发 ASR/翻译。"""


def test_latency_stats_survive_session_reaping(settings):
    from app.connection import SessionRegistry
    from app.telemetry import SentenceRecord

    registry = SessionRegistry(settings, _Stub(), _Stub())
    session, _ = registry.get_or_create("s-1")
    assert session.stats is registry.latency

    session.stats.add(
        SentenceRecord(
            sentence_id=1,
            audio_ms=1000,
            queue_wait_ms=0,
            asr_ms=100,
            translate_ms=200,
            total_ms=1500,
        )
    )
    # 模拟会话过期被回收
    registry._sessions.pop("s-1", None)
    registry._detached_since.pop("s-1", None)

    summary = registry.latency.summary()
    assert summary["sentences"] == 1
    assert summary["total_ms_max"] == 1500


def test_new_session_continues_accumulating(settings):
    from app.connection import SessionRegistry
    from app.telemetry import SentenceRecord

    registry = SessionRegistry(settings, _Stub(), _Stub())
    for sid in ("a", "b"):
        session, _ = registry.get_or_create(sid)
        session.stats.add(
            SentenceRecord(
                sentence_id=1,
                audio_ms=1000,
                queue_wait_ms=0,
                asr_ms=100,
                translate_ms=200,
                total_ms=2000,
            )
        )
        registry._sessions.pop(sid, None)

    assert registry.latency.summary()["sentences"] == 2
