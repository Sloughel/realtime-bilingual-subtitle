"""字幕历史 JSONL 写入测试（默认关闭、开启后逐行追加、失败不抛出）。"""

from __future__ import annotations

import json


async def test_history_appends_jsonl(tmp_path):
    from app.history import SubtitleHistory

    path = tmp_path / "subs.jsonl"
    history = SubtitleHistory(path, enabled=True)
    await history.append(
        {"session_id": "s-1", "sentence_id": 1, "source": "Hello", "translation": "你好"}
    )
    await history.append(
        {"session_id": "s-1", "sentence_id": 2, "source": "World", "translation": "世界"}
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["source"] == "Hello"
    assert first["translation"] == "你好"
    assert first["sentence_id"] == 1
    assert "ts" in first  # 本地时区 ISO 时间戳
    assert json.loads(lines[1])["sentence_id"] == 2


async def test_history_disabled_writes_nothing(tmp_path):
    from app.history import SubtitleHistory

    path = tmp_path / "subs.jsonl"
    history = SubtitleHistory(path, enabled=False)
    await history.append({"source": "Hello", "translation": "你好"})
    assert not path.exists()


async def test_history_write_failure_is_non_fatal(tmp_path):
    from app.history import SubtitleHistory

    # 用一个同名目录制造 OSError（IsADirectoryError），append 不应抛出
    path = tmp_path / "subs.jsonl"
    path.mkdir()
    history = SubtitleHistory(path, enabled=True)
    await history.append({"source": "Hello", "translation": "你好"})
