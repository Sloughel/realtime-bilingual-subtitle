"""ASR 验证脚本：用测试音频验证 faster-whisper + CUDA 推理链路。

用法：
    .venv/Scripts/python.exe scripts/verify_asr.py [音频文件]

默认使用 server/tests/fixtures/sample_en.wav。
输出转写文本与各阶段耗时，用于核对 NFR-01 中"单句 ASR ≤800ms"的预算。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = PROJECT_ROOT / "server" / "tests" / "fixtures" / "sample_en.wav"


def load_env() -> None:
    """加载 server/.env，使其中的配置在本脚本中生效。"""
    env_file = PROJECT_ROOT / "server" / ".env"
    if not env_file.is_file():
        return
    from dotenv import load_dotenv

    load_dotenv(env_file, override=False)


def add_nvidia_dll_dirs() -> None:
    nvidia = PROJECT_ROOT / ".venv" / "Lib" / "site-packages" / "nvidia"
    if not nvidia.is_dir():
        return
    for sub in ("cublas", "cudnn", "cuda_nvrtc"):
        bin_dir = nvidia / sub / "bin"
        if bin_dir.is_dir():
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]


def resolve_model(model: str) -> str:
    """模型名可能是规格名（medium），也可能是相对项目根目录的本地路径。"""
    candidate = PROJECT_ROOT / model
    if (candidate / "model.bin").is_file():
        return str(candidate)
    return model


def main() -> int:
    load_env()
    add_nvidia_dll_dirs()

    audio_path = Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURE
    if not audio_path.is_file():
        print(f"找不到音频文件：{audio_path}")
        return 1

    from faster_whisper import WhisperModel

    model_name = resolve_model(os.environ.get("WHISPER_MODEL", "medium"))
    device = os.environ.get("WHISPER_DEVICE", "cuda")
    compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", "float16")

    print("=" * 68)
    print("ASR 链路验证 — faster-whisper")
    print("=" * 68)
    print(f"音频文件   : {audio_path}  ({audio_path.stat().st_size / 1024:.1f} KB)")
    print(f"模型       : {model_name}")
    print(f"设备 / 精度 : {device} / {compute_type}")

    t0 = time.perf_counter()
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    load_ms = (time.perf_counter() - t0) * 1000
    print(f"模型加载    : {load_ms:.0f} ms")

    t0 = time.perf_counter()
    segments, info = model.transcribe(
        str(audio_path),
        language="en",
        beam_size=1,
        vad_filter=False,  # VAD 由上游断句器负责
        condition_on_previous_text=False,
    )
    segments = list(segments)
    asr_ms = (time.perf_counter() - t0) * 1000

    print(f"音频时长    : {info.duration:.2f} s")
    print(f"识别耗时    : {asr_ms:.0f} ms  (实时率 {info.duration / (asr_ms / 1000):.1f}x)")
    print(f"识别语言    : {info.language}  (置信度 {info.language_probability:.2f})")
    print("-" * 68)
    print("转写结果：")
    for seg in segments:
        print(f"  [{seg.start:6.2f}s → {seg.end:6.2f}s]  {seg.text.strip()}")
    print("-" * 68)

    if not segments:
        print("结论：未识别出任何文本，请检查音频内容。")
        return 1

    budget_ms = 800.0
    per_sentence = asr_ms / max(len(segments), 1)
    verdict = "通过" if per_sentence <= budget_ms else "超出预算"
    print(f"结论：识别成功，平均 {per_sentence:.0f} ms/段，NFR-01 ASR 预算 {budget_ms:.0f} ms — {verdict}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
