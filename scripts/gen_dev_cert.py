"""生成自签名证书，用于本地验证 wss（E1）。

用法：
    python scripts/gen_dev_cert.py                    # 默认 SAN: localhost / 127.0.0.1
    python scripts/gen_dev_cert.py --ip 192.168.1.10  # 额外加入本机局域网 IP
    python scripts/gen_dev_cert.py --ip 10.243.9.41 --days 825

产出：
    certs/dev-cert.pem   证书（PEM）
    certs/dev-key.pem    私钥（PEM）

然后把脚本打印的两行写进 server/.env：

    SSL_CERTFILE=certs/dev-cert.pem
    SSL_KEYFILE=certs/dev-key.pem

注意（写给答辩时被问到的情况）：
    * 自签名证书只适合**开发联调**。Android 默认信任系统 CA，不会信任自签名证书，
      真机调试需要把证书装进用户证书库（设置 → 安全 → 安装证书），或在 App 里
      配置自定义 TrustManager（当前客户端未实现）。
    * 生产/公网场景请用 frp + nginx 或云厂商证书，让隧道/反向代理终结 TLS，
      服务端保持明文 ws 只监听回环或内网——这也是 02 文档 §9 的推荐做法。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CERT_DIR = PROJECT_ROOT / "certs"


def main() -> int:
    ap = argparse.ArgumentParser(description="生成本地 wss 调试用自签名证书")
    ap.add_argument("--ip", action="append", default=[], help="额外的 IP SAN，可重复")
    ap.add_argument("--cn", default="bilingual-subtitle-dev", help="证书 CN")
    ap.add_argument("--days", type=int, default=365, help="有效期（天）")
    ap.add_argument("--out-dir", default=str(CERT_DIR), help="输出目录")
    args = ap.parse_args()

    if shutil.which("openssl") is None:
        print("[错误] 未找到 openssl。", file=sys.stderr)
        print("       Ubuntu/Debian: sudo apt install openssl", file=sys.stderr)
        print("       macOS: 自带 LibreSSL/OpenSSL", file=sys.stderr)
        print("       Windows: 可用 Git for Windows 自带的 openssl", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    cert = out_dir / "dev-cert.pem"
    key = out_dir / "dev-key.pem"

    ips = ["127.0.0.1", *args.ip]
    san = "DNS:localhost," + ",".join(f"IP:{ip}" for ip in ips)

    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key),
        "-out", str(cert),
        "-days", str(args.days),
        "-subj", f"/CN={args.cn}",
        "-addext", f"subjectAltName={san}",
    ]
    print("执行：", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("[错误] openssl 执行失败：", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        return result.returncode

    print()
    print("=" * 68)
    print(f"已生成证书：{cert}")
    print(f"           {key}")
    print(f"SAN      ：{san}")
    print("=" * 68)
    # 私钥只给属主读（与 ssh 私钥同级要求）
    try:
        os.chmod(key, 0o600)
    except OSError as exc:  # Windows 上 chmod 语义有限，忽略
        print(f"[提示] 未能修改私钥权限（{exc}），请自行确认仅本人可读。")
    print("把下面两行加入 server/.env（或直接 export 为环境变量）后重启服务端：")
    print(f"SSL_CERTFILE={cert.relative_to(PROJECT_ROOT) if cert.is_relative_to(PROJECT_ROOT) else cert}")
    print(f"SSL_KEYFILE={key.relative_to(PROJECT_ROOT) if key.is_relative_to(PROJECT_ROOT) else key}")
    print()
    print("客户端连接地址改为： wss://<主机IP>:8000/ws?token=…")
    print("自签名证书的信任问题见本脚本文件头部说明。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
