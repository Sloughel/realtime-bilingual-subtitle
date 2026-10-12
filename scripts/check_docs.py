"""文档一致性检查：章节编号连续性、markdown 链接有效性、版本声明一致性、交叉引用。

《05-项目状态与交接说明》§9 的文档改动规范要求：改章节编号后须校验跨文档引用与
链接有效性，条目编号不得缺号。本脚本把该检查自动化，便于每次改动后复跑。

判定分级：
* **FAIL（必须修）**：章节编号缺号、链接目标不存在、变更记录版本与 05 文档声明不一致、
  **带文档标识**的引用（如 `03 文档 §9.3`）在目标文档中不存在。
* **WARN（需人看）**：无文档标识的裸 `§N` 在本文件内找不到。表格单元格里的裸 `§N`
  常常属于同一张表另一列所指的文档（例：01 文档 §3.3 表的最后一列表头写明"03 文档"），
  无法由文本自动判定，故列为警告而非失败。

生成物 `AI协作记录.md`（由脚本还原的对话转录，非设计文档）不参与引用与链接检查。

用法::

    .venv/Scripts/python.exe scripts/check_docs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted((ROOT / "docs").glob("*.md"))
TRANSCRIPT = "AI协作记录.md"
CHECKED = [p for p in DOCS if p.name != TRANSCRIPT] + [ROOT / "README.md"]

HEAD_RE = re.compile(r"^(#{2,4})\s+(\d+(?:\.\d+)*)[.、\s]")
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
FENCE_RE = re.compile(r"```.*?```", re.S)
# 带文档标识的引用：01 文档 §9.3 / 01-需求规格 §2.3 / 01、§2.3
TAGGED_REF_RE = re.compile(r"0([1-5])\s*(?:-|文档|、)\s*§\s*(\d+(?:\.\d+)*)")
BARE_REF_RE = re.compile(r"§\s*(\d+(?:\.\d+)*)")
CHANGELOG_HEAD_RE = re.compile(r"^##\s+\d+[.、]\s*变更记录", re.M)
CHANGELOG_ROW_RE = re.compile(r"^\|\s*v(\d+)\.(\d+)", re.M)
VERSION_LINE_RE = re.compile(r"(0[1-4])[^\s`]*\s*`(v\d+\.\d+)`")

DOC_MAP = {p.name[:2]: p for p in DOCS}


def strip_code(text: str) -> str:
    return FENCE_RE.sub("", text)


def headings(text: str) -> set[str]:
    return {
        m.group(2)
        for line in strip_code(text).splitlines()
        if (m := HEAD_RE.match(line.strip()))
    }


def top_numbers(text: str) -> list[int]:
    return [
        int(m.group(1))
        for line in strip_code(text).splitlines()
        if (m := re.match(r"^##\s+(\d+)[.、\s]", line.strip()))
    ]


def sub_numbers(text: str) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for line in strip_code(text).splitlines():
        m = re.match(r"^###\s+(\d+)\.(\d+)", line.strip())
        if m:
            groups.setdefault(m.group(1), []).append(int(m.group(2)))
    return groups


def last_version(text: str) -> str | None:
    """只从「变更记录」小节里取版本号，避免误匹配正文中的 17.0.20.1 之类。"""
    m = CHANGELOG_HEAD_RE.search(text)
    section = text[m.start() :] if m else ""
    best: tuple[int, int] | None = None
    for a, b in CHANGELOG_ROW_RE.findall(section):
        cand = (int(a), int(b))
        if best is None or cand > best:
            best = cand
    return f"v{best[0]}.{best[1]}" if best else None


def main() -> int:
    fails: list[str] = []
    warns: list[str] = []

    heading_cache = {p: headings(p.read_text(encoding="utf-8")) for p in DOCS}

    # ---- 1. 章节编号连续性 ----
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8")
        tops = top_numbers(text)
        if tops and tops != list(range(1, len(tops) + 1)):
            fails.append(f"{doc.name}: 顶层章节编号不连续 {tops}")
        for parent, subs in sub_numbers(text).items():
            if subs != list(range(1, len(subs) + 1)):
                fails.append(f"{doc.name}: §{parent} 子章节编号不连续 {subs}")

    # ---- 2. 交叉引用 ----
    for doc in CHECKED:
        text = strip_code(doc.read_text(encoding="utf-8"))
        for m in TAGGED_REF_RE.finditer(text):
            target = DOC_MAP.get("0" + m.group(1))
            num = m.group(2)
            if target is None:
                fails.append(f"{doc.name}: `{m.group(0)}` 指向未知文档")
            elif num not in heading_cache[target]:
                fails.append(
                    f"{doc.name}: `{m.group(0)}` 在 {target.name} 中不存在"
                )
        # 裸 §N：本文件内找不到 → 警告（可能属于表格另一列所指文档）
        for m in BARE_REF_RE.finditer(text):
            span = m.span()
            # 跳过紧跟在文档标识后的（已由 TAGGED_REF 覆盖）
            pre = text[max(0, span[0] - 8) : span[0]]
            if re.search(r"0[1-5]\s*(?:-|文档|、)\s*$", pre):
                continue
            num = m.group(1)
            if num not in heading_cache.get(doc, set()):
                warns.append(f"{doc.name}: 裸引用 §{num} 在本文件内无对应章节")

    # ---- 3. markdown 链接 ----
    for doc in CHECKED:
        text = strip_code(doc.read_text(encoding="utf-8"))
        for target in LINK_RE.findall(text):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = (doc.parent / target.split("#")[0]).resolve()
            if not path.exists():
                fails.append(f"{doc.name}: 链接目标不存在 {target}")

    # ---- 4. 版本声明一致性 ----
    handout = DOC_MAP.get("05")
    claimed = (
        dict(VERSION_LINE_RE.findall(handout.read_text(encoding="utf-8")))
        if handout
        else {}
    )
    for key, doc in DOC_MAP.items():
        if key == "05":
            continue
        actual = last_version(doc.read_text(encoding="utf-8"))
        if key in claimed and claimed[key] != actual:
            fails.append(
                f"{doc.name}: 变更记录最新版本 {actual} 与 05 文档声明 {claimed[key]} 不一致"
            )

    # ---- 报告 ----
    print("=" * 72)
    print("文档一致性检查")
    print("=" * 72)
    print(f"检查文件：{', '.join(p.name for p in CHECKED)}")
    print(f"（{TRANSCRIPT} 为生成的转录，不参与引用/链接检查）")
    print(f"版本声明：{claimed}")
    print("-" * 72)
    for f in fails:
        print(f"[FAIL] {f}")
    for w in warns:
        print(f"[WARN] {w}")
    print("-" * 72)
    print(f"FAIL {len(fails)} 处，WARN {len(warns)} 处。")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
