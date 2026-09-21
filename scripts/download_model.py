"""模型下载脚本：把 faster-whisper 模型权重下载到项目本地目录。

默认从 hf-mirror.com（HuggingFace 国内镜像）下载，避开 huggingface.co 的
网络限制；脚本自带断点续传，中断后重跑即可。

用法：
    .venv/Scripts/python.exe scripts/download_model.py            # 默认 medium
    .venv/Scripts/python.exe scripts/download_model.py large-v3
    .venv/Scripts/python.exe scripts/download_model.py small --endpoint https://huggingface.co

下载结果放在 models/faster-whisper-<size>/，可用 .env 的 WHISPER_MODEL 指向该目录。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")

# 只需要 CTranslate2 推理所需的文件，README/.gitattributes 等跳过
WANTED = {"config.json", "model.bin", "tokenizer.json", "vocabulary.txt", "vocabulary.json",
          "preprocessor_config.json"}

# 列表接口不可用时的兜底清单（faster-whisper 各规格仓库的文件组成）
FALLBACK_FILES = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]

MAX_RETRY = 3


class _Redirect308Handler(urllib.request.HTTPRedirectHandler):
    """urllib 默认不跟随 308，而 hf-mirror 的 API 会返回 308。"""

    def http_error_308(self, req, fp, code, msg, headers):  # noqa: N802
        return self.http_error_301(req, fp, 301, msg, headers)


_opener = urllib.request.build_opener(_Redirect308Handler)


def _open(req: urllib.request.Request, timeout: int = 60):
    """带重试的请求，用于吸收偶发的 SSL 握手超时。"""
    last: Exception | None = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            return _opener.open(req, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except Exception as exc:  # noqa: BLE001 — 网络类异常统一重试
            last = exc
            if attempt < MAX_RETRY:
                delay = 2 ** attempt
                sys.stdout.write(f"\n  请求失败（{exc}），{delay}s 后重试…\n")
                sys.stdout.flush()
                time.sleep(delay)
    raise last  # type: ignore[misc]


def list_repo_files(endpoint: str, repo: str, timeout: int = 30) -> list[str]:
    url = f"{endpoint}/api/models/{repo}"
    req = urllib.request.Request(url, headers={"User-Agent": "bilingual-subtitle/0.1"})
    with _open(req, timeout=timeout) as resp:
        data = json.load(resp)
    return [s["rfilename"] for s in data.get("siblings", [])]


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _have_curl() -> bool:
    try:
        subprocess.run(["curl", "--version"], capture_output=True, timeout=15, check=True)
        return True
    except Exception:  # noqa: BLE001
        return False


CURL_AVAILABLE = _have_curl()


def _download_with_curl(url: str, tmp: Path) -> None:
    """用 curl 传输：实测比 Python urllib 快数倍（同一镜像 0.5MB/s vs 4MB/s）。

    `-C -` 让 curl 自行从已有文件处续传，`--fail` 让 HTTP 错误变成非零退出码。
    """
    cmd = ["curl", "-L", "--fail", "-C", "-", "--no-progress-meter", "-o", str(tmp), url]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"curl 退出码 {proc.returncode}: {proc.stderr.strip()[:200]}")


def _download_with_urllib(url: str, tmp: Path) -> None:
    done = tmp.stat().st_size if tmp.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "bilingual-subtitle/0.1"})
    if done:
        req.add_header("Range", f"bytes={done}-")

    try:
        resp = _open(req)
    except urllib.error.HTTPError as exc:
        if exc.code == 416:  # 已下载完整
            return
        raise

    total = done + int(resp.headers.get("Content-Length") or 0)
    mode = "ab" if done and resp.status == 206 else "wb"
    if mode == "wb":
        done = 0

    with open(tmp, mode) as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 / total
                sys.stdout.write(f"\r  {'model':<12} {pct:5.1f}%  {human(done)}/{human(total)}")
            else:
                sys.stdout.write(f"\r  {'model':<12} {human(done)}")
            sys.stdout.flush()
    sys.stdout.write("\n")


def download_file(endpoint: str, repo: str, filename: str, dest: Path) -> None:
    url = f"{endpoint}/{repo}/resolve/main/{filename}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    before = tmp.stat().st_size if tmp.exists() else 0

    print(f"  {filename:<24} {human(before)} 已存在" if before else f"  {filename:<24} 开始下载")
    sys.stdout.flush()

    if CURL_AVAILABLE:
        _download_with_curl(url, tmp)
    else:
        _download_with_urllib(url, tmp)

    if not tmp.exists():
        raise RuntimeError(f"{filename} 下载后文件不存在")
    tmp.replace(dest)
    print(f"  {filename:<24} 完成 {human(dest.stat().st_size)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="下载 faster-whisper 模型")
    ap.add_argument("size", nargs="?", default="medium",
                    help="模型规格：tiny/base/small/medium/large-v3（默认 medium）")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="下载端点，默认 hf-mirror.com")
    args = ap.parse_args()

    repo = f"Systran/faster-whisper-{args.size}"
    dest_dir = PROJECT_ROOT / "models" / f"faster-whisper-{args.size}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 68)
    print(f"下载模型 {repo}")
    print(f"端点     {args.endpoint}")
    print(f"目标目录 {dest_dir}")
    print("=" * 68)

    try:
        files = [f for f in list_repo_files(args.endpoint, repo) if f in WANTED]
    except Exception as exc:  # noqa: BLE001
        print(f"获取文件列表失败（{exc}），改用固定文件清单继续。")
        files = list(FALLBACK_FILES)

    if not files:
        print("仓库中没有找到预期文件，请检查模型名。")
        return 1

    print(f"待下载 {len(files)} 个文件：{', '.join(sorted(files))}\n")
    for name in sorted(files, key=lambda n: -1 if n == "model.bin" else 0):
        target = dest_dir / name
        if target.exists():
            print(f"  {name:<24} 已存在，跳过 ({human(target.stat().st_size)})")
            continue
        download_file(args.endpoint, repo, name, target)

    total = sum(f.stat().st_size for f in dest_dir.iterdir() if f.is_file())
    print("-" * 68)
    print(f"完成，共 {human(total)}，位于 {dest_dir}")
    print(f"提示：在 server/.env 中设置 WHISPER_MODEL={dest_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
