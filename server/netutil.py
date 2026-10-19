"""网络与端口工具：本机地址枚举、ZeroTier 识别、端口占用检测。

被 `run_server.py` 与 `stop_server.py` 复用。
"""

from __future__ import annotations

import errno
import socket


def default_route_ip() -> str:
    """取默认路由的出口地址（UDP connect 只做路由选择，不实际发包）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def is_private_ip(ip: str) -> bool:
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
    primary = default_route_ip()
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
        if is_private_ip(ip):
            return (2, ip)
        return (3, ip)

    return sorted(found, key=rank)


def port_in_use(host: str, port: int) -> bool:
    """用不带 SO_REUSEADDR 的 bind 试探端口是否已被占用。"""
    bind_host = "0.0.0.0" if host in ("", "0.0.0.0") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((bind_host, port))
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            # 端口被占用，或其他原因无法绑定，都视为不可启动
            if exc.errno == errno.EADDRINUSE or winerror == 10048:
                return True
            return True
        return False


def port_owner(port: int) -> tuple[int, str, str] | None:
    """返回监听该端口的进程 (pid, name, exe)；需要 psutil。"""
    try:
        import psutil
    except ImportError:
        return None
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status != psutil.CONN_LISTEN or conn.laddr is None:
                continue
            if conn.laddr.port != port or conn.pid is None:
                continue
            try:
                proc = psutil.Process(conn.pid)
                return conn.pid, proc.name(), proc.exe()
            except Exception:  # noqa: BLE001
                return conn.pid, "?", "?"
    except Exception:  # noqa: BLE001 - 无权限等情况
        return None
    return None
