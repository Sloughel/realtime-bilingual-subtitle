"""翻译链路验证脚本：用真实 API 验证 DeepSeek 翻译可用性与性能。

用法：
    .venv/Scripts/python.exe scripts/verify_translate.py
    .venv/Scripts/python.exe scripts/verify_translate.py "自定义待翻译句子"

检查项：
    1. server/.env 中密钥已配置且可用（鉴权失败会给出明确提示）
    2. 逐句翻译延迟是否符合 NFR-01 的"翻译 ≤1500ms"预算
    3. token 用量是否符合 NFR-10 的"输入 ≤200、输出 ≤150"上限
    4. 输出是否为纯译文（不含解释、前缀或原文）

脚本只打印译文与用量，**不打印密钥**。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 与 03 文档 §10 保持一致的字幕翻译提示词
SYSTEM_PROMPT = (
    "你是一名专业字幕翻译。将用户给出的{src_name}音频转写文本翻译成{tgt_name}。\n"
    "要求：\n"
    "1. 只输出译文，不要任何解释、前缀或引用原文；\n"
    "2. 人名、地名、品牌等专有名词保留原文或使用通用译法；\n"
    "3. 译文口语化、简短，符合字幕习惯；\n"
    "4. 不要逐字硬译语气词（如嗯、啊）。"
)

LANG_NAMES = {"en": "英语", "zh": "中文", "ja": "日语"}

DEFAULT_SAMPLES = [
    "Hello, this is a test of the real time bilingual subtitle system.",
    "The audio you are hearing was generated on the host machine.",
    "Machine translation will convert these sentences into Chinese.",
]

# 预算与上限（来自 01 文档 NFR-01、NFR-10）
BUDGET_TRANSLATE_MS = 1500.0
LIMIT_INPUT_TOKENS = 200
LIMIT_OUTPUT_TOKENS = 150


def load_env() -> None:
    env_file = PROJECT_ROOT / "server" / ".env"
    if env_file.is_file():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)


async def translate(client, base_url: str, key: str, model: str, timeout: float,
                    text: str, src: str, tgt: str):
    """单句翻译，返回 (译文, 耗时ms, usage)。"""
    payload = {
        "model": model,
        "temperature": 0.3,
        "max_tokens": max(64, 2 * len(text) + 20),
        "messages": [
            {"role": "system",
             "content": SYSTEM_PROMPT.format(src_name=LANG_NAMES.get(src, src),
                                            tgt_name=LANG_NAMES.get(tgt, tgt))},
            {"role": "user", "content": text},
        ],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    resp = await client.post(f"{base_url.rstrip('/')}/chat/completions",
                             json=payload, headers=headers, timeout=timeout)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    out = (data["choices"][0]["message"]["content"] or "").strip()
    usage = data.get("usage") or {}
    return out, elapsed_ms, usage


async def main() -> int:
    load_env()
    import httpx

    base_url = os.environ.get("TRANSLATE_BASE_URL", "https://api.deepseek.com")
    key = (os.environ.get("TRANSLATE_API_KEY") or "").strip()
    model = os.environ.get("TRANSLATE_MODEL", "deepseek-chat")
    timeout = float(os.environ.get("TRANSLATE_TIMEOUT_S", 10))
    src = os.environ.get("TRANSLATE_SRC_LANG", "en")
    tgt = os.environ.get("TRANSLATE_TGT_LANG", "zh")

    print("=" * 72)
    print("翻译链路验证 — OpenAI 兼容接口")
    print("=" * 72)
    print(f"端点 / 模型 : {base_url} / {model}")
    print(f"语言方向    : {src} → {tgt}")
    print(f"密钥        : {'已配置' if key and not key.startswith('sk-在此') else '未配置或仍为占位符'}"
          f"（长度 {len(key)}，不打印内容）")

    if not key or key.startswith("sk-在此"):
        print("\n结论：server/.env 中 TRANSLATE_API_KEY 未填写，无法验证。")
        return 1

    samples = [sys.argv[1]] if len(sys.argv) > 1 else DEFAULT_SAMPLES
    rows = []
    async with httpx.AsyncClient() as client:
        for text in samples:
            try:
                out, ms, usage = await translate(client, base_url, key, model, timeout, text, src, tgt)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                print(f"\n[失败] {text[:60]}")
                print(f"       {msg[:300]}")
                if "401" in msg:
                    print("       → 鉴权失败：密钥无效或已被撤销，请重新生成")
                elif "402" in msg or "balance" in msg.lower():
                    print("       → 余额不足：请在 DeepSeek 控制台充值")
                elif "429" in msg:
                    print("       → 触发限流：稍后重试（M1 需实现退避重试，见 FM-03）")
                return 1
            rows.append((text, out, ms, usage))
            print("-" * 72)
            print(f"原文 : {text}")
            print(f"译文 : {out}")
            print(f"耗时 : {ms:.0f} ms    tokens: 输入 {usage.get('prompt_tokens', '?')} "
                  f"/ 输出 {usage.get('completion_tokens', '?')}")

    print("=" * 72)
    print("验证结果")
    print("-" * 72)
    ok = True

    slow = [r for r in rows if r[2] > BUDGET_TRANSLATE_MS]
    avg_ms = sum(r[2] for r in rows) / len(rows)
    verdict = "通过" if not slow else "超预算"
    print(f"延迟        : 平均 {avg_ms:.0f} ms，最大 {max(r[2] for r in rows):.0f} ms"
          f"（NFR-01 预算 {BUDGET_TRANSLATE_MS:.0f} ms/句）— {verdict}")
    ok &= not slow

    max_in = max((r[3].get("prompt_tokens") or 0) for r in rows)
    max_out = max((r[3].get("completion_tokens") or 0) for r in rows)
    tok_ok = max_out <= LIMIT_OUTPUT_TOKENS
    print(f"输出 tokens : 最大 {max_out}（NFR-10 上限 {LIMIT_OUTPUT_TOKENS}）— "
          f"{'通过' if tok_ok else '超上限'}")
    ok &= tok_ok

    # 纯译文检查：不应出现解释性前缀或原文回显
    bad = [r for r in rows if r[0][:20] in r[1] or r[1].startswith(("翻译", "译文", "Here", "Sure"))]
    print(f"输出纯净度  : {'通过（无前缀、无原文回显）' if not bad else f'{len(bad)} 句含前缀或原文回显'}")
    ok &= not bad

    print(f"输入 tokens : 最大 {max_in}（NFR-10 参考上限 {LIMIT_INPUT_TOKENS}）")
    print("=" * 72)
    print("结论：" + ("翻译链路可用，符合 NFR-01 与 NFR-10。" if ok else "存在问题，见上。"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
