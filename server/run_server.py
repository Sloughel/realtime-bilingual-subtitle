"""服务端启动入口（供 `start_server.bat` 或命令行使用）。

用法::

    .venv/Scripts/python.exe server/run_server.py

等价于在 `server/` 目录下执行::

    ../.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

配置来自 `server/.env`（SERVER_HOST / SERVER_PORT / WHISPER_* / TRANSLATE_* 等）。
启动后会打印手机端应填写的主机局域网 IP。
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import uvicorn  # noqa: E402

from app.main import app  # noqa: E402
from app.settings import APP_VERSION, get_settings  # noqa: E402


def _default_route_ip() -> str:
    """取默认路由的出口地址（UDP connect 只做路由选择，不实际发包）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _is_private(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return False
    a, b = int(parts[0]), int(parts[1])
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def zerotier_ip() -> str | None:
    """返回 ZeroTier 虚拟网卡的 IPv4（存在且已启用时）。

    依赖 psutil；未安装时静默返回 None，不影响启动（候选列表仍会包含该地址）。
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        stats = psutil.net_if_stats()
        for name, addrs in psutil.net_if_addrs().items():
            if "zerotier" not in name.lower():
                continue
            if name in stats and not stats[name].isup:
                continue
            for addr in addrs:
                if (
                    addr.family == socket.AF_INET
                    and addr.address
                    and not addr.address.startswith(("127.", "169.254."))
                ):
                    return addr.address
    except Exception:  # noqa: BLE001 - 识别失败不应影响启动
        return None
    return None


def ipv4_candidates() -> list[str]:
    """列出可供手机连接的 IPv4 候选。

    排序：**ZeroTier 地址优先**（便于跨网络联调）→ 默认路由出口 → 其他私网地址。
    自动排除回环与 169.254.*（未获取到 DHCP 的链路本地地址）。
    本机若装有 VMware / VirtualBox / VPN，其地址仍会出现在候选里，需要部署者排除。
    """
    primary = _default_route_ip()
    zt = zerotier_ip()
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127.") or ip.startswith("169.254."):
                continue
            found.add(ip)
    except OSError:
        pass
    if not primary.startswith(("127.", "169.254.")):
        found.add(primary)
    if zt:
        found.add(zt)

    def rank(ip: str) -> tuple[int, str]:
        if zt and ip == zt:
            return (0, ip)
        if ip == primary:
            return (1, ip)
        if _is_private(ip):
            return (2, ip)
        return (3, ip)

    return sorted(found, key=rank)


def main() -> int:
    settings = get_settings()
    candidates = ipv4_candidates()
    zt = zerotier_ip()
    primary = candidates[0] if candidates else "127.0.0.1"
    print("=" * 66)
    print(f"实时双语字幕服务端  v{APP_VERSION}")
    print("=" * 66)
    print(f"监听地址   : {settings.server_host}:{settings.server_port}")
    hint = "ZeroTier 虚拟局域网" if zt and primary == zt else "默认路由出口"
    print(f"手机端填写 : {primary}:{settings.server_port}   （{hint}）")
    if some := candidates[1:]:
        print(f"其他候选   : {', '.join(some)}")
    if zt:
        print(f"ZeroTier   : {zt}（手机需安装 ZeroTier One 并加入同一网络）")
    print(
        f"             若手机连不上：用浏览器访问 "
        f"http://{primary}:{settings.server_port}/health 自检；"
    )
    print("             并以管理员身份运行 allow_firewall.bat 放行 8000/TCP（仅 ZeroTier 网段）。")
    print(f"识别设备   : {settings.whisper_device} / {settings.whisper_compute_type}")
    print(
        f"翻译后端   : {settings.translate_base_url} / {settings.translate_model} "
        f"({'已配置密钥' if settings.translate_configured else '未配置，将降级为原文'})"
    )
    print("状态接口   : /health 、/status")
    print("首次启动需加载 whisper 模型（约 10~30 秒）…")
    print("按 Ctrl+C 停止。")
    print("=" * 66)
    if primary == "127.0.0.1":
        print("提示：未获取到可用 IPv4，请确认已连接 Wi-Fi/网线。")
    print("提示：若手机连不上，请检查 Windows 防火墙是否放行 Python 的 8000/TCP 端口。")
    print()

    uvicorn.run(
        app,
        host=settings.server_host,
        port=settings.server_port,
        log_level=settings.log_level.lower(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
