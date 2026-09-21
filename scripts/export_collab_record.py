"""AI 协作记录提取脚本。

把 ZCode 的会话日志（model-io-*.jsonl）整理成一份适合提交的 Markdown 协作记录：
按「用户提示词 → 助手回复 → 工具调用」的时间顺序排列，过滤掉内部噪音。

保留：用户每次发出的提示词、助手的正文回复、工具调用清单（工具名 + 参数摘要）、
      模型调用失败记录（体现迭代与排错过程）
过滤：system prompt、system-reminder 注入内容、推理过程、工具返回的原始内容

用法：
    .venv/Scripts/python.exe scripts/export_collab_record.py
    .venv/Scripts/python.exe scripts/export_collab_record.py --session 812a17c4
    .venv/Scripts/python.exe scripts/export_collab_record.py --input <路径> --max-chars 0

默认自动选取 ~/.zcode/cli/rollout 下最新的会话文件。

────────────────────────────────────────────────────────────────────────
日志格式说明（排查后确认，供后续维护参考）

每条记录 = 一次模型调用。日志里有两种承载对话的字段，取决于版本：

  1. 旧格式：request.body.messages
     仅当请求带 experimental_include.requestBody 时出现。

  2. 新格式：request.messages，并附带三个关键字段：
        messageOffset  本条 messages[0] 在整个会话中的全局下标
        messageCount   调用发生时整个会话的消息总数
        messagesKind   full（全量）/ delta（增量）/ tail（滑动窗口，约 64 条）

     因此**单条记录看不到完整对话**：会话变长后只记录尾部窗口。
     本脚本按 messageOffset 把所有记录的窗口按全局下标拼接，还原完整时间线；
     同一全局下标以首次出现的版本为准，以保留未压缩的原始内容。

另外，工具调用的存放位置也随格式不同：
    旧格式  assistant.content 里含 {"type": "tool-call", "toolName", "input"}
    新格式  assistant 消息的 toolCalls 字段，形如 [{"id", "name", "input"}]
    两种都已在 build_entries 中处理。
────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROLLOUT_DIR = Path.home() / ".zcode" / "cli" / "rollout"

REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
# 后台任务完成通知等由系统注入的 user 消息，不是用户本人写的提示词
INJECTED_BLOCK_RE = re.compile(r"<(?:system-reminder|task-notification)>.*?</(?:system-reminder|task-notification)>", re.S)
STRAY_TAG_RE = re.compile(r"</?(?:system-reminder|task-notification|task-id)\b[^>]*>")
ARG_KEYS = ("command", "file_path", "path", "pattern", "query", "url", "text", "prompt")


def log(msg: str) -> None:
    print(msg, flush=True)


def find_session_file(session: str | None) -> Path:
    files = sorted(ROLLOUT_DIR.glob("model-io-sess_*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise SystemExit(f"未找到会话日志：{ROLLOUT_DIR}")
    if session:
        matched = [f for f in files if session in f.name]
        if not matched:
            raise SystemExit(f"没有匹配 '{session}' 的会话文件。可用：\n  " +
                             "\n  ".join(f.name for f in files))
        return matched[0]
    return files[-1]


def fmt_ts(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return iso


def clean_user_text(text: str) -> str:
    """剥掉系统注入的内容，只留用户真正输入的提示词。"""
    text = INJECTED_BLOCK_RE.sub("", text)
    text = STRAY_TAG_RE.sub("", text)
    return text.strip()


def summarize_tool_call(tool_name: str, tool_input) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for k in ARG_KEYS:
        v = tool_input.get(k)
        if isinstance(v, str) and v.strip():
            one_line = " ".join(v.split())
            if len(one_line) > 110:
                one_line = one_line[:110] + "…"
            return one_line
    keys = ", ".join(list(tool_input)[:4])
    return f"({keys})" if keys else ""


def collect_records(path: Path) -> list[dict]:
    recs: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return recs


def extract_messages(rec: dict) -> tuple[list[dict], int, str] | None:
    """取出本条记录的对话窗口，返回 (messages, offset, kind)。"""
    req = rec.get("request") or {}
    msgs = req.get("messages")
    if isinstance(msgs, list) and msgs:
        return msgs, int(req.get("messageOffset") or 0), str(req.get("messagesKind") or "?")
    body = req.get("body") or {}
    if isinstance(body, dict):
        msgs = body.get("messages")
        if isinstance(msgs, list) and msgs:
            return msgs, 0, "legacy-body"
    return None


def stitch_conversation(recs: list[dict]) -> tuple[dict[int, tuple[dict, str]], dict]:
    """把所有记录的对话窗口按全局下标拼接成完整会话。

    同一全局下标保留**首次出现**的版本（原始内容优先于后续的压缩摘要）。
    返回 {全局下标: (消息, 首次出现时间)}。
    """
    slots: dict[int, tuple[dict, str]] = {}
    stats = {"records": 0, "with_messages": 0, "kinds": {}, "max_count": 0}

    for rec in recs:
        stats["records"] += 1
        got = extract_messages(rec)
        if not got:
            continue
        msgs, offset, kind = got
        stats["with_messages"] += 1
        stats["kinds"][kind] = stats["kinds"].get(kind, 0) + 1
        req = rec.get("request") or {}
        count = int(req.get("messageCount") or (offset + len(msgs)))
        stats["max_count"] = max(stats["max_count"], count)

        ts = rec.get("startedAt") or ""
        for j, msg in enumerate(msgs):
            if not isinstance(msg, dict):
                continue
            gi = offset + j
            if gi not in slots:
                slots[gi] = (msg, ts)

    return slots, stats


def build_entries(slots: dict[int, tuple[dict, str]], recs: list[dict]) -> tuple[list[dict], dict]:
    """按全局下标顺序把消息转成条目，并把模型调用异常插入时间线。"""
    entries: list[dict] = []
    stats = {"user": 0, "assistant": 0, "tools": 0, "errors": 0,
             "skipped_reminder": 0, "reasoning": 0, "system": 0, "tool_results": 0}

    # 异常列表按时间排序，遇到不晚于当前消息时间的就先行输出
    errors: list[tuple[str, dict]] = []
    for rec in recs:
        err = rec.get("error")
        if isinstance(err, dict) and err.get("message"):
            errors.append((rec.get("startedAt") or "", err))
    errors.sort(key=lambda x: x[0])
    err_i = 0

    def emit_errors_before(ts: str) -> None:
        nonlocal err_i
        while err_i < len(errors) and errors[err_i][0] <= ts:
            _, err = errors[err_i]
            err_i += 1
            stats["errors"] += 1
            entries.append({"kind": "error", "ts": err.get("startedAt") or errors[err_i - 1][0],
                            "name": err.get("name", "Error"),
                            "message": str(err.get("message"))[:200]})

    pending_tools: list[tuple[str, str]] = []
    pending_ts: str | None = None
    pending_text: list[str] = []
    pending_text_ts: str | None = None

    def flush_tools() -> None:
        nonlocal pending_tools, pending_ts
        if pending_tools:
            entries.append({"kind": "tools", "ts": pending_ts, "items": pending_tools})
            stats["tools"] += len(pending_tools)
            pending_tools = []
            pending_ts = None

    def flush_text() -> None:
        nonlocal pending_text, pending_text_ts
        if pending_text:
            entries.append({"kind": "assistant", "ts": pending_text_ts,
                            "text": "\n\n".join(pending_text).strip()})
            stats["assistant"] += 1
            pending_text = []
            pending_text_ts = None

    for gi in sorted(slots):
        msg, ts = slots[gi]
        role = msg.get("role")
        content = msg.get("content")
        emit_errors_before(ts)

        if role == "system":
            stats["system"] += 1
            continue
        if role == "tool":
            stats["tool_results"] += 1
            continue

        if role == "user":
            raw = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            text = clean_user_text(raw)
            if not text:
                stats["skipped_reminder"] += 1
                continue
            flush_text()
            flush_tools()
            stats["user"] += 1
            entries.append({"kind": "user", "ts": ts, "text": text})
            continue

        if role != "assistant":
            continue

        parts = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        for p in parts:
            if not isinstance(p, dict):
                continue
            ptype = p.get("type")
            if ptype == "tool-call":
                flush_text()
                if pending_ts is None:
                    pending_ts = ts
                pending_tools.append((p.get("toolName") or "?",
                                      summarize_tool_call(p.get("toolName") or "", p.get("input"))))
            elif ptype == "reasoning":
                stats["reasoning"] += 1
            elif ptype == "text":
                text = (p.get("text") or "").strip()
                if not text:
                    continue
                flush_tools()
                if pending_text_ts is None:
                    pending_text_ts = ts
                pending_text.append(text)

        # 新格式：工具调用挂在消息的 toolCalls 字段上，而不是 content 的 part 里
        for tc in (msg.get("toolCalls") or []):
            if not isinstance(tc, dict):
                continue
            name = tc.get("name") or "?"
            flush_text()
            if pending_ts is None:
                pending_ts = ts
            pending_tools.append((name, summarize_tool_call(name, tc.get("input"))))

    flush_text()
    flush_tools()
    emit_errors_before("9999")
    return entries, stats


def truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"\n\n*（此处省略 {len(text) - limit} 字，完整内容见原始日志）*"


def render(entries: list[dict], stats: dict, conv: dict, src: Path,
           session_id: str, max_chars: int) -> str:
    times = [e["ts"] for e in entries if e.get("ts")]
    span = f"{fmt_ts(min(times))} ~ {fmt_ts(max(times))}" if times else "（无时间信息）"

    out: list[str] = []
    out.append("# AI 协作记录")
    out.append("")
    out.append("> 本记录由 ZCode 会话日志自动提取，用于提交课程作业。")
    out.append(">")
    out.append(f"> - 会话 ID：`{session_id}`")
    out.append(f"> - 时间范围：{span}")
    out.append(f"> - 数据来源：`{src.name}`")
    out.append(f"> - 统计：日志记录（模型调用）{conv['records']} 条，"
               f"其中带对话窗口 {conv['with_messages']} 条；"
               f"还原会话消息 {stats['user'] + stats['assistant'] + stats['system'] + stats['tool_results']} 条")
    out.append(f"> - 提取结果：用户提示词 **{stats['user']} 条**、助手回复 {stats['assistant']} 段、"
               f"工具调用 {stats['tools']} 次、模型调用异常 {stats['errors']} 次")
    out.append(">")
    out.append("> **提取说明**：仅保留用户提示词、助手正文回复与工具调用摘要；"
               "系统内部提示词（system prompt）、注入的 system-reminder、推理过程、"
               "以及工具返回的原始内容均已过滤。"
               f"助手回复超过 {max_chars} 字的部分做截断标注。")
    out.append("")

    rounds: list[dict] = []
    for e in entries:
        if e["kind"] == "user" or not rounds:
            rounds.append({"user": e if e["kind"] == "user" else None, "items": []})
        if e["kind"] != "user":
            rounds[-1]["items"].append(e)

    # 首条用户提示词之前的事件（如会话初始化失败）单独成节，避免出现无编号的孤立轮次
    leading: list[dict] = []
    while rounds and rounds[0]["user"] is None:
        leading.extend(rounds[0]["items"])
        rounds.pop(0)

    out.append("## 轮次索引")
    out.append("")
    if leading:
        out.append("> 会话开始前有 %d 条系统事件，见文末附注。" % len(leading))
        out.append("")
    out.append("| 轮次 | 时间 | 用户提示词（摘要） |")
    out.append("|---|---|---|")
    idx = 0
    for r in rounds:
        if r["user"]:
            idx += 1
            r["no"] = idx
            summary = " ".join(r["user"]["text"].split())
            if len(summary) > 48:
                summary = summary[:48] + "…"
            out.append(f"| {idx} | {fmt_ts(r['user']['ts'])[5:16]} | {summary.replace('|', chr(92) + '|')} |")
    out.append("")

    for r in rounds:
        if r.get("user"):
            out.append(f"## 轮 {r['no']:02d}　{fmt_ts(r['user']['ts'])}")
            out.append("")
            out.append("### 👤 用户提示词")
            out.append("")
            out.append(r["user"]["text"])
            out.append("")
        for e in r["items"]:
            if e["kind"] == "assistant":
                out.append("### 🤖 助手回复")
                out.append("")
                out.append(truncate(e["text"], max_chars))
                out.append("")
            elif e["kind"] == "tools":
                out.append(f"### 🔧 工具调用（{len(e['items'])} 次）")
                out.append("")
                for name, arg in e["items"]:
                    out.append(f"- `{name}` {arg}".rstrip())
                out.append("")
            elif e["kind"] == "error":
                out.append(f"### ⚠ 模型调用异常：{e['name']}")
                out.append("")
                out.append(e["message"])
                out.append("")

    # 首条用户提示词之前发生的系统事件，作为附注列在文末
    if leading:
        out.append("## 附注：会话开始前的系统事件")
        out.append("")
        for e in leading:
            if e["kind"] == "error":
                out.append(f"- ⚠ 模型调用异常 `{e['name']}`（{fmt_ts(e['ts'])}）：{e['message']}")
            elif e["kind"] == "tools":
                out.append(f"- 🔧 工具调用 {len(e['items'])} 次")
            elif e["kind"] == "assistant":
                out.append(f"- 🤖 助手回复：{truncate(e['text'], 200)}")
        out.append("")

    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="从 ZCode 会话日志提取 AI 协作记录")
    ap.add_argument("--input", help="model-io-*.jsonl 路径")
    ap.add_argument("--session", help="按会话 ID 片段选择（如 812a17c4）")
    ap.add_argument("--output", help="输出 Markdown 路径，默认 docs/AI协作记录.md")
    ap.add_argument("--max-chars", type=int, default=2000,
                    help="助手回复保留的最大字数（0 表示不截断），默认 2000")
    args = ap.parse_args()

    src = Path(args.input) if args.input else find_session_file(args.session)
    if not src.is_file():
        raise SystemExit(f"日志文件不存在：{src}")

    m = re.search(r"model-io-(sess_[0-9a-f\-]+)\.jsonl", src.name)
    session_id = m.group(1) if m else src.stem

    log(f"读取日志：{src}")
    recs = collect_records(src)
    slots, conv = stitch_conversation(recs)
    log(f"  日志记录 {conv['records']} 条｜带对话窗口 {conv['with_messages']} 条"
        f"（类型分布 {conv['kinds']}）")
    log(f"  按 offset 拼接后还原会话消息 {len(slots)} 条（会话总长度 {conv['max_count']}）")

    entries, stats = build_entries(slots, recs)
    log(f"  用户提示词 {stats['user']} 条｜助手回复 {stats['assistant']} 段｜"
        f"工具调用 {stats['tools']} 次｜异常 {stats['errors']} 次")
    if stats["skipped_reminder"]:
        log(f"  已过滤仅含 system-reminder 的非用户消息 {stats['skipped_reminder']} 条")
    if len(slots) < conv["max_count"]:
        log(f"  注意：还原 {len(slots)} 条 < 会话总长 {conv['max_count']} 条，"
            f"差额为滑动窗口未覆盖或已被上下文压缩的部分")

    md = render(entries, stats, conv, src, session_id, args.max_chars)
    out = Path(args.output) if args.output else PROJECT_ROOT / "docs" / "AI协作记录.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    log(f"已生成：{out}  （{len(md):,} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
