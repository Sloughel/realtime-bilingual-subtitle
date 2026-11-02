"""E2 本地翻译链路验证：对真实的 Ollama 端点做一次"自检 + 翻译"。

用法（服务端虚拟环境）：
    .venv/bin/python scripts/verify_ollama.py             # Linux / macOS
    .venv\\Scripts\\python.exe scripts/verify_ollama.py    # Windows
    python scripts/verify_ollama.py "自定义待翻译句子"

检查项：
    1. TRANSLATE_BACKEND 是否为 ollama（否则提示先改配置）
    2. Ollama 服务是否可达（GET /api/tags）
    3. 配置的模型是否已 pull（缺失时给出 ollama pull 命令）
    4. 真实翻译三句，报告延迟与 token 用量
    5. 输出是否为纯译文（无解释、无原文回显）

**隐私含义**：本脚本只会访问 OLLAMA_BASE_URL，不会访问任何外部服务；
可用抓包确认无外网流量（NFR-11）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = PROJECT_ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

DEFAULT_SAMPLES = [
    "Hello, this is a test of the real time bilingual subtitle system.",
    "The audio you are hearing was generated on the host machine.",
    "Machine translation will convert these sentences into Chinese.",
]


def main() -> int:
    import httpx

    from app.settings import Settings
    from app.translate import build_prompt

    settings = Settings()
    base = settings.ollama_base_url.rstrip("/")

    print("=" * 72)
    print("本地翻译链路验证 — Ollama（E2）")
    print("=" * 72)
    print(f"TRANSLATE_BACKEND : {settings.translate_backend}")
    print(f"端点              : {base}")
    print(f"模型              : {settings.ollama_model}")
    print(f"超时预算          : {settings.ollama_timeout_s:.0f} s")

    if not settings.translate_is_local:
        print(
            "\n结论：当前后端不是 ollama。请在 server/.env 设置 "
            "TRANSLATE_BACKEND=ollama 后重试。"
        )
        return 1

    samples = [sys.argv[1]] if len(sys.argv) > 1 else DEFAULT_SAMPLES
    # 本地模型不需要密钥，故不传 Authorization
    headers = {"Content-Type": "application/json"}

    with httpx.Client(timeout=settings.ollama_timeout_s) as client:
        # ---- 1. 服务可达性 + 模型是否已 pull ----
        try:
            tags = client.get(f"{base}/api/tags")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[失败] 无法连接 Ollama：{exc}")
            print("       → 先启动服务：`ollama serve`（或 systemctl start ollama）")
            print(f"       → 再确认 OLLAMA_BASE_URL={base} 与监听端口一致")
            return 1

        if tags.status_code != 200:
            print(f"\n[失败] /api/tags 返回 HTTP {tags.status_code}: {tags.text[:200]}")
            return 1

        names = [m.get("name", "") for m in (tags.json().get("models") or [])]
        print(f"已安装模型        : {', '.join(names) if names else '（无）'}")
        target = settings.ollama_model
        present = any(n == target or n.split(":")[0] == target.split(":")[0] for n in names)
        if not present:
            print(f"\n[失败] 未找到模型 {target}")
            print(f"       → 执行：ollama pull {target}")
            return 1

        # ---- 2. 真实翻译 ----
        rows: list[tuple[str, str, float, dict]] = []
        import time

        for text in samples:
            payload = {
                "model": target,
                "temperature": 0.3,
                "max_tokens": max(64, 2 * len(text) + 20),
                "messages": [
                    {"role": "system", "content": build_prompt("en", "zh")},
                    {"role": "user", "content": text},
                ],
            }
            t0 = time.perf_counter()
            try:
                resp = client.post(f"{base}/v1/chat/completions", json=payload, headers=headers)
            except Exception as exc:  # noqa: BLE001
                print(f"\n[失败] 翻译请求异常：{exc}")
                return 1
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if resp.status_code != 200:
                print(f"\n[失败] 翻译接口 HTTP {resp.status_code}: {resp.text[:200]}")
                return 1
            data = resp.json()
            out = (data["choices"][0]["message"]["content"] or "").strip()
            usage = data.get("usage") or {}
            if not out:
                print(f"\n[失败] 返回内容为空：{text[:60]}")
                return 1
            rows.append((text, out, elapsed_ms, usage))
            print("-" * 72)
            print(f"原文 : {text}")
            print(f"译文 : {out}")
            print(f"耗时 : {elapsed_ms:.0f} ms    tokens: 输入 "
                  f"{usage.get('prompt_tokens', '?')} / 输出 {usage.get('completion_tokens', '?')}")

    # ---- 3. 结论 ----
    print("=" * 72)
    lat = [r[2] for r in rows]
    print(f"延迟        : 平均 {sum(lat) / len(lat):.0f} ms，最大 {max(lat):.0f} ms")
    print("              （原生 Ollama 未做流式/批处理，普遍慢于云端接口；"
          "NFR-01 的 800/1500ms 预算按云端后端设定）")
    echoes = [r for r in rows if r[0][:20] in r[1] or r[1].startswith(("翻译", "译文", "Here", "Sure"))]
    print(f"输出纯净度  : {'通过（无前缀、无原文回显）' if not echoes else f'{len(echoes)} 句含前缀或原文回显'}")
    print("隐私        : 本次验证仅访问 " + base + "，未发出任何外网请求（NFR-11）")
    print("=" * 72)
    ok = not echoes
    print("结论：" + ("本地翻译链路可用。" if ok else "译文质量需调整提示词或换模型，见上。"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
