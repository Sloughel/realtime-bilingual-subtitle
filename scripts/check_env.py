"""环境自检脚本：验证服务端运行环境是否就绪。

用法：
    .venv/Scripts/python.exe scripts/check_env.py

检查项：
    1. 关键依赖版本（faster-whisper / ctranslate2 / onnxruntime / fastapi）
    2. NVIDIA CUDA 运行时 DLL 是否就位（cuBLAS / cuDNN）
    3. CTranslate2 能否看到 CUDA 设备
    4. 配置项是否可加载、翻译 API 是否配置了密钥
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_NVIDIA = PROJECT_ROOT / ".venv" / "Lib" / "site-packages" / "nvidia"


def add_nvidia_dll_dirs() -> list[str]:
    """把 pip 安装的 NVIDIA DLL 目录加入 PATH。

    CTranslate2 在 Windows 上通过 PATH 查找 cuBLAS / cuDNN，pip 安装的
    nvidia-*-cu12 包不会自动登记这些目录，需要在导入前手动加入。
    """
    added: list[str] = []
    if not VENV_NVIDIA.is_dir():
        return added
    for sub in ("cublas", "cudnn", "cuda_nvrtc"):
        bin_dir = VENV_NVIDIA / sub / "bin"
        if bin_dir.is_dir():
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]
            added.append(str(bin_dir))
    return added


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "OK  " if ok else "FAIL"
    print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    print("=" * 68)
    print("实时双语字幕系统 — 服务端环境自检")
    print("=" * 68)
    print(f"Python      : {sys.version.split()[0]}  ({sys.executable})")
    print(f"项目根目录  : {PROJECT_ROOT}")

    results: list[bool] = []

    # ---- 1. 依赖版本 ----
    print("\n--- 1. 依赖检查 ---")
    ver: dict[str, str] = {}
    for mod in ("faster_whisper", "ctranslate2", "onnxruntime", "fastapi", "httpx", "numpy"):
        try:
            m = __import__(mod)
            ver[mod] = getattr(m, "__version__", "unknown")
            results.append(check(mod, True, ver[mod]))
        except Exception as exc:  # noqa: BLE001
            ver[mod] = "-"
            results.append(check(mod, False, f"导入失败: {exc}"))

    # ---- 2. NVIDIA DLL ----
    print("\n--- 2. CUDA 运行时 DLL ---")
    added = add_nvidia_dll_dirs()
    results.append(check("NVIDIA DLL 目录", bool(added), f"已注册 {len(added)} 个目录"))
    for d in added:
        print(f"       {d}")

    # ---- 3. CUDA 设备 ----
    print("\n--- 3. CUDA 设备可见性 ---")
    if ver.get("ctranslate2", "-") != "-":
        try:
            import ctranslate2

            n = ctranslate2.get_cuda_device_count()
            results.append(check("CTranslate2 CUDA 设备数", n > 0, str(n)))
            if n > 0:
                print(f"       支持的 compute types: {ctranslate2.get_supported_compute_types('cuda')}")
        except Exception as exc:  # noqa: BLE001
            results.append(check("CTranslate2 CUDA 设备数", False, f"查询异常: {exc}"))
    else:
        results.append(check("CTranslate2 CUDA 设备数", False, "ctranslate2 未安装"))

    # ---- 4. 配置 ----
    print("\n--- 4. 配置检查 ---")
    env_file = PROJECT_ROOT / "server" / ".env"
    has_env = env_file.is_file()
    results.append(check("server/.env 存在", has_env,
                         str(env_file) if has_env else "尚未创建（可从 .env.example 复制）"))
    if has_env:
        from dotenv import dotenv_values

        cfg = dotenv_values(env_file)
        key = (cfg.get("TRANSLATE_API_KEY") or "").strip()
        configured = bool(key) and not key.startswith("sk-在此")
        results.append(check("TRANSLATE_API_KEY 已填写", configured,
                             "已配置" if configured else "仍是占位值，翻译功能不可用"))
        print(f"       WHISPER_MODEL      = {cfg.get('WHISPER_MODEL', '(默认 medium)')}")
        print(f"       WHISPER_DEVICE     = {cfg.get('WHISPER_DEVICE', '(默认 cuda)')}")
        print(f"       TRANSLATE_BASE_URL = {cfg.get('TRANSLATE_BASE_URL', '(默认 DeepSeek)')}")

    # ---- 汇总 ----
    print("\n" + "=" * 68)
    failed = results.count(False)
    if failed == 0:
        print("结论：环境就绪，可以进入 M1 服务端开发。")
    else:
        print(f"结论：{failed} 项未通过，见上方 [FAIL] 标记。")
    print("=" * 68)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
