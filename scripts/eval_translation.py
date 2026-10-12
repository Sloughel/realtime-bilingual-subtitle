"""翻译质量抽检工具 —— NFR-03 的翻译侧判据。

NFR-03（翻译）要求：抽取连续 100 句，由 **2 名评审独立判定语义是否正确**，
一致通过率 ≥ 95%；分歧句由第 3 人裁定。

本脚本负责**可自动化的部分**：把 100 句送真实翻译后端，产出
① 自动预筛指标（空译文、原文回显、目标语言字符、长度比异常等），
② 供人工填写的评审表，③ 延迟与 token/成本统计。

**自动预筛不能替代人工语义判定**，最终 ≥95% 结论仍须由 2 名评审完成。

用法::

    .venv/Scripts/python.exe scripts/eval_translation.py
    .venv/Scripts/python.exe scripts/eval_translation.py --limit 20 --concurrency 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = PROJECT_ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

CORPUS = SERVER_DIR / "tests" / "fixtures" / "corpus_en.txt"
TARGET_CONSISTENCY = 0.95
BUDGET_TRANSLATE_MS = 1500.0

CJK = re.compile(r"[\u4e00-\u9fff]")


def load_corpus(path: Path, limit: int = 0) -> list[str]:
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    return lines[:limit] if limit else lines


def prescreen(source: str, translation: str, tgt_lang: str) -> list[str]:
    """自动预筛：返回问题列表（空表示无异常）。"""
    issues: list[str] = []
    t = translation.strip()
    if not t:
        issues.append("空译文")
        return issues
    if t == source.strip():
        issues.append("译文与原文完全相同")
    # 原文回显：译文中出现原文的前 6 个词
    head = " ".join(source.split()[:6]).lower()
    if len(head) > 10 and head in t.lower():
        issues.append("疑似原文回显")
    if tgt_lang == "zh" and not CJK.search(t):
        issues.append("译文中无中文字符")
    ratio = len(t) / max(len(source), 1)
    if ratio < 0.15 or ratio > 3.0:
        issues.append(f"长度比异常（{ratio:.2f}）")
    return issues


async def run(args) -> int:
    from app.settings import get_settings
    from app.translate import DeepSeekTranslator

    settings = get_settings()
    if not settings.translate_configured:
        print("server/.env 中 TRANSLATE_API_KEY 未配置，无法评测。")
        return 1

    sentences = load_corpus(Path(args.corpus), args.limit)
    translator = DeepSeekTranslator(settings)
    await translator.warm_up()
    sem = asyncio.Semaphore(args.concurrency)
    src, tgt = args.src, args.tgt

    print("=" * 78)
    print("翻译质量抽检 —— NFR-03 翻译判据")
    print("=" * 78)
    print(f"语料   : {args.corpus}（{len(sentences)} 句）")
    print(f"后端   : {settings.translate_base_url} / {settings.translate_model}   {src} → {tgt}")
    print(f"并发   : {args.concurrency}    密钥：已配置（长度 {settings.api_key_length}，不打印）")
    print("-" * 78)

    async def one(idx: int, text: str) -> dict:
        async with sem:
            outcome = await translator.translate(text, src, tgt)
        return {
            "index": idx,
            "source": text,
            "translation": outcome.text,
            "degraded": outcome.degraded,
            "error_code": outcome.error_code,
            "elapsed_ms": outcome.elapsed_ms,
            "usage": outcome.usage,
            "issues": prescreen(text, outcome.text, tgt),
        }

    t0 = time.perf_counter()
    rows = await asyncio.gather(*(one(i + 1, s) for i, s in enumerate(sentences)))
    wall_s = time.perf_counter() - t0
    await translator.aclose()

    # ---- 统计 ----
    lat = [r["elapsed_ms"] for r in rows if not r["degraded"]]
    tok_in = sum((r["usage"].get("prompt_tokens") or 0) for r in rows)
    tok_out = sum((r["usage"].get("completion_tokens") or 0) for r in rows)
    cost = tok_in / 1e6 * settings.price_in_per_m + tok_out / 1e6 * settings.price_out_per_m
    clean = [r for r in rows if not r["issues"]]
    degraded = [r for r in rows if r["degraded"]]
    flagged = [r for r in rows if r["issues"]]

    print(f"完成     : {len(rows)} 句，墙钟 {wall_s:.1f}s（平均 {wall_s / len(rows):.2f}s/句）")
    if lat:
        print(f"延迟     : 平均 {statistics.fmean(lat):.0f}ms  "
              f"P95 {sorted(lat)[int(len(lat) * 0.95) - 1]}ms  最大 {max(lat)}ms"
              f"   （预算 ≤ {BUDGET_TRANSLATE_MS:.0f}ms）")
    print(f"降级句   : {len(degraded)}")
    print(f"自动预筛 : 无异常 {len(clean)}/{len(rows)}，异常 {len(flagged)} 条")
    for r in flagged[:15]:
        print(f"    #{r['index']:>3} {r['issues']}  | {r['source'][:44]}")
    print(f"token    : 输入 {tok_in}，输出 {tok_out}（约 {cost:.4f} 元，按配置单价估算）")

    # ---- 人工评审表 ----
    out_md = PROJECT_ROOT / args.out_md
    out_md.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 翻译质量评审表（NFR-03）",
        "",
        f"- 语料：`{Path(args.corpus).name}`，共 {len(rows)} 句；方向 {src} → {tgt}",
        f"- 后端：{settings.translate_base_url} / {settings.translate_model}",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 判定标准：2 名评审独立判定**语义是否正确**，一致通过率 ≥ {TARGET_CONSISTENCY:.0%}；"
        "分歧句由第 3 人裁定。",
        "- 「自动预筛」列仅供参考，不能替代人工判定。",
        "",
        "| # | 原文 | 译文 | 自动预筛 | 评审1 (√/×) | 评审2 (√/×) | 裁定 (√/×) | 备注 |",
        "|---|------|------|----------|-------------|-------------|------------|------|",
    ]
    for r in rows:
        issues = "；".join(r["issues"]) if r["issues"] else "—"
        src_c = r["source"].replace("|", "\\|")
        tgt_c = r["translation"].replace("|", "\\|")
        lines.append(f"| {r['index']} | {src_c} | {tgt_c} | {issues} |  |  |  |  |")
    lines += [
        "",
        "## 汇总（评审后填写）",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 总句数 | {len(rows)} |",
        "| 评审1 通过 |  |",
        "| 评审2 通过 |  |",
        "| 一致通过句数 |  |",
        "| 一致通过率 |  |",
        f"| 判定（目标 ≥ {TARGET_CONSISTENCY:.0%}） |  |",
        "",
    ]
    out_md.write_text("\n".join(lines), encoding="utf-8")

    out_json = PROJECT_ROOT / args.out_json
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(
            {
                "corpus": str(args.corpus),
                "count": len(rows),
                "src_lang": src,
                "tgt_lang": tgt,
                "latency_ms": {"avg": round(statistics.fmean(lat), 1) if lat else 0, "max": max(lat) if lat else 0},
                "tokens": {"in": tok_in, "out": tok_out, "cost_yuan_est": round(cost, 4)},
                "degraded": len(degraded),
                "prescreen_flagged": len(flagged),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("-" * 78)
    print(f"评审表已写出：{out_md}")
    print(f"明细已写出  ：{out_json}")
    print("注意：自动预筛仅做粗筛；NFR-03 的 ≥95% 结论须由 2 名评审完成一致性判定。")
    print("=" * 78)
    return 0 if not degraded and len(clean) == len(rows) else 0  # 预筛异常不视为失败


def main() -> int:
    ap = argparse.ArgumentParser(description="翻译质量抽检（NFR-03）")
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--src", default="en")
    ap.add_argument("--tgt", default="zh")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--out-md", default="logs/translation_eval.md")
    ap.add_argument("--out-json", default="logs/translation_eval.json")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
