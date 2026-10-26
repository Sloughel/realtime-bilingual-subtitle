"""查看服务端字幕历史（logs/subtitles.jsonl）。

前置：在 ``server/.env`` 设置 ``SUBTITLE_HISTORY=true`` 并重启服务端；每句字幕会追加
一行 JSON（含原文/译文/耗时）。文件已被 ``.gitignore`` 排除，请勿提交。

用法::

    # 最近 20 条（默认）
    .venv/Scripts/python.exe scripts/show_history.py

    # 全部；按会话过滤
    .venv/Scripts/python.exe scripts/show_history.py --tail 0
    .venv/Scripts/python.exe scripts/show_history.py --session d2ff

    # 原始 JSON 行（便于用 jq/Python 二次处理）
    .venv/Scripts/python.exe scripts/show_history.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = PROJECT_ROOT / "logs" / "subtitles.jsonl"


def _short(text: str, limit: int = 80) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _load(path: Path, tail: int) -> list[dict]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        lines = deque(f, maxlen=None if tail <= 0 else tail)
    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="查看服务端字幕历史（JSONL）")
    ap.add_argument("--path", default=str(DEFAULT_PATH), help="JSONL 路径")
    ap.add_argument("--tail", type=int, default=20, help="最近 N 条；0 表示全部")
    ap.add_argument("--session", default="", help="按 session_id 子串过滤")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON 行")
    args = ap.parse_args()

    path = Path(args.path)
    if not path.is_file():
        print(f"找不到字幕历史文件：{path}")
        print("请先在 server/.env 设置 SUBTITLE_HISTORY=true，然后重启服务端。")
        return 1

    records = _load(path, args.tail)
    if args.session:
        records = [r for r in records if args.session in str(r.get("session_id", ""))]

    if not records:
        print(f"{path} 中没有匹配的记录。")
        return 1

    if args.json:
        for rec in records:
            print(json.dumps(rec, ensure_ascii=False))
        return 0

    print(f"文件：{path}")
    print(f"显示最近 {len(records)} 条（--tail 0 查看全部）")
    print("=" * 72)
    for rec in records:
        ts = str(rec.get("ts", "")).replace("T", " ")[:23]
        sid = str(rec.get("session_id", ""))[:8]
        n = rec.get("sentence_id", "?")
        mode = rec.get("mode", "?")
        total = rec.get("total_ms", "?")
        flag = " [翻译失败，已回传原文]" if rec.get("translation_degraded") else ""
        print(f"[{ts}] 会话 {sid}…  #{n}  {mode}  {total}ms{flag}")
        print(f"  原文: {_short(rec.get('source', ''))}")
        print(f"  译文: {_short(rec.get('translation', ''))}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
