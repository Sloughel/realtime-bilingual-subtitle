"""Windows 下 CUDA 运行时 DLL 注册。

CTranslate2 在 Windows 上通过 ``PATH`` 查找 cuBLAS / cuDNN；pip 安装的
``nvidia-*-cu12`` 包**不会**自动登记这些目录，必须在**导入 ctranslate2 /
faster_whisper 之前**调用 :func:`register`（见《05-项目状态与交接说明》§3 第 1 条）。

注意：``os.add_dll_directory`` 必须传入**绝对路径**，相对路径会抛 ``WinError 87``。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SUBDIRS = ("cublas", "cudnn", "cuda_nvrtc")

_registered: list[str] = []


def _candidate_site_packages() -> list[Path]:
    roots: list[Path] = []
    try:
        import site

        roots.extend(Path(p) for p in site.getsitepackages())
    except Exception:  # noqa: BLE001 - 某些精简环境没有 site
        pass
    pyver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    roots.extend(
        [
            Path(sys.prefix) / "Lib" / "site-packages",  # Windows 布局
            Path(sys.prefix) / "lib" / pyver / "site-packages",  # Linux 布局
        ]
    )
    for entry in sys.path:
        if entry:
            roots.append(Path(entry))
    # 去重且保持顺序
    seen: set[str] = set()
    unique: list[Path] = []
    for r in roots:
        key = str(r)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def register() -> list[str]:
    """把可找到的 NVIDIA DLL 目录登记到进程。

    返回已登记的绝对目录列表；非 Windows 平台或未安装 nvidia 包时返回空列表。
    可安全重复调用。
    """
    if os.name != "nt":
        return _registered
    for root in _candidate_site_packages():
        nvidia = root / "nvidia"
        if not nvidia.is_dir():
            continue
        for sub in _SUBDIRS:
            bin_dir = nvidia / sub / "bin"
            if not bin_dir.is_dir():
                continue
            abs_dir = bin_dir.resolve()
            key = str(abs_dir)
            if key in _registered:
                continue
            try:
                os.add_dll_directory(key)
            except OSError:
                # 目录存在但登记失败时仍继续，PATH 追加可能已足够
                pass
            os.environ["PATH"] = key + os.pathsep + os.environ.get("PATH", "")
            _registered.append(key)
    return _registered
