"""ASR 词错率（WER）评测脚本 —— NFR-03 的识别侧判据。

在「音频 + 人工标注文本」成对语料上计算 WER，并在多个文件上汇总。
NFR-03 要求：100 句英文标准口音语料（总时长 ≥10 分钟）上 **WER ≤ 10%**。

语料组织（默认目录 ``server/tests/fixtures``）::

    <name>.wav   # 任意采样率，脚本内部统一重采样为 16kHz 单声道
    <name>.txt   # 与音频对应的**人工标注**文本（一行或多行，逐句）

用法::

    .venv/Scripts/python.exe scripts/eval_asr_wer.py
    .venv/Scripts/python.exe scripts/eval_asr_wer.py --dir path/to/corpus

说明：标注必须**独立于 ASR 输出**（人工听写或使用生成音频时的原始文本），否则
评测失去意义。仓库内现成的 ``sample_en.txt`` 即 TTS 合成时的原始文本。
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = PROJECT_ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from app import dll  # noqa: E402

dll.register()

WER_TARGET = 0.10  # NFR-03


def normalize(text: str) -> list[str]:
    """归一化：小写、去标点、折叠空白，返回词序列（标点与大小写差异不计错）。"""
    text = text.lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    return [w for w in text.split() if w]


def edit_distance(ref: list[str], hyp: list[str]) -> tuple[int, int, int]:
    """返回 (替换, 删除, 插入) 三个计数。"""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    op = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        d[i][0] = i
        op[i][0] = "D"
    for j in range(1, m + 1):
        d[0][j] = j
        op[0][j] = "I"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                d[i][j] = d[i - 1][j - 1]
                op[i][j] = "="
                continue
            cand = (
                (d[i - 1][j - 1] + 1, "S"),
                (d[i - 1][j] + 1, "D"),
                (d[i][j - 1] + 1, "I"),
            )
            d[i][j], op[i][j] = min(cand)
    sub = dele = ins = 0
    i, j = n, m
    while i > 0 or j > 0:
        o = op[i][j]
        if o == "S":
            sub += 1
            i -= 1
            j -= 1
        elif o == "D":
            dele += 1
            i -= 1
        elif o == "I":
            ins += 1
            j -= 1
        else:
            i -= 1
            j -= 1
    return sub, dele, ins


def main() -> int:
    ap = argparse.ArgumentParser(description="ASR WER 评测（NFR-03）")
    ap.add_argument("--dir", default=str(SERVER_DIR / "tests" / "fixtures"))
    ap.add_argument("--model", default=None, help="覆盖 WHISPER_MODEL")
    args = ap.parse_args()

    corpus_dir = Path(args.dir)
    pairs = sorted(
        (wav, wav.with_suffix(".txt"))
        for wav in corpus_dir.glob("*.wav")
        if wav.with_suffix(".txt").is_file()
    )
    if not pairs:
        print(f"目录中未找到 wav/txt 成对语料：{corpus_dir}")
        return 1

    from app.settings import get_settings
    from app.asr import ASREngine

    settings = get_settings()
    if args.model:
        settings.whisper_model = args.model
    engine = ASREngine(settings)
    try:
        engine.load()
    except Exception as exc:  # noqa: BLE001
        print(f"模型加载失败：{exc}")
        return 1

    from faster_whisper.audio import decode_audio

    print("=" * 78)
    print("ASR 词错率（WER）评测 —— NFR-03 识别判据")
    print("=" * 78)
    print(f"语料目录 : {corpus_dir}")
    print(f"模型     : {engine.settings.whisper_model_path}（device={engine.device}）")
    print("-" * 78)

    total_sub = total_del = total_ins = total_ref = 0
    total_audio_s = 0.0
    for wav, txt in pairs:
        reference_text = txt.read_text(encoding="utf-8")
        audio = decode_audio(str(wav), sampling_rate=16000).astype(np.float32)
        t0 = time.perf_counter()
        hypothesis = engine.transcribe(audio, "en")
        ms = (time.perf_counter() - t0) * 1000
        total_audio_s += audio.size / 16000

        ref = normalize(reference_text)
        hyp = normalize(hypothesis)
        sub, dele, ins = edit_distance(ref, hyp)
        wer = (sub + dele + ins) / max(len(ref), 1)
        total_sub += sub
        total_del += dele
        total_ins += ins
        total_ref += len(ref)

        print(f"{wav.name}  ({audio.size / 16000:.1f}s, {ms:.0f}ms)")
        print(f"  标注 : {reference_text.strip().replace(chr(10), ' / ')}")
        print(f"  识别 : {hypothesis}")
        print(f"  WER  : {wer * 100:.1f}%   (S={sub} D={dele} I={ins}, 参考词数 {len(ref)})")

    corpus_wer = (total_sub + total_del + total_ins) / max(total_ref, 1)
    print("-" * 78)
    print(f"语料时长 : {total_audio_s:.1f}s（NFR-03 要求 ≥600s/100 句）")
    print(f"汇总 WER : {corpus_wer * 100:.1f}%   目标 ≤ {WER_TARGET * 100:.0f}%")
    ok = corpus_wer <= WER_TARGET
    print(f"判定     : {'PASS' if ok else 'FAIL'}")
    if total_audio_s < 600:
        print(f"注意     : 当前语料仅 {total_audio_s:.0f}s，未达 NFR-03 的 ≥10 分钟要求；")
        print("           此项为工具验证，正式判定需补足标注语料（M3/M5 阶段）。")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
