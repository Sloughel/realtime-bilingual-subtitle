"""网络配置工具：查看/修改服务端监听地址、端口与防火墙放行范围。

用法::

    .venv/Scripts/python.exe server/netconfig.py            # 交互菜单
    .venv/Scripts/python.exe server/netconfig.py --show     # 只看当前配置
    .venv/Scripts/python.exe server/netconfig.py --firewall any|zerotier|off
    .venv/Scripts/python.exe server/netconfig.py --set-port 8001
    .venv/Scripts/python.exe server/netconfig.py --set-host 0.0.0.0

防火墙操作需要管理员权限；推荐双击仓库根目录的 `network_config.bat`（会自动请求 UAC）。
"""

from __future__ import annotations

import argparse
import ctypes
import ipaddress
import re
import socket
import subprocess
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from netutil import default_route_ip, zerotier_ip  # noqa: E402

ENV_FILE = SERVER_DIR / ".env"
RULE_NAME = "Realtime Bilingual Subtitle"
LEGACY_RULE_NAMES = (RULE_NAME, "Realtime Bilingual Subtitle (ZeroTier)")
ZERO_TIER_SUBNET = "10.243.9.0/24"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.is_file():
        return values
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def update_env(key: str, value: str) -> None:
    """按行替换 .env 中的键，保留注释与顺序；不存在则追加。"""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.is_file() else []
    out: list[str] = []
    found = False
    for line in lines:
        if re.match(rf"^\s*{re.escape(key)}\s*=", line):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


def interfaces() -> list[dict]:
    """列出已启用的 IPv4 网卡：名称、地址、子网、是否 ZeroTier/虚拟。"""
    try:
        import psutil
    except ImportError:
        return []
    result: list[dict] = []
    stats = psutil.net_if_stats()
    for name, addrs in psutil.net_if_addrs().items():
        if name not in stats or not stats[name].isup:
            continue
        for addr in addrs:
            if addr.family != socket.AF_INET or addr.address.startswith("127."):
                continue
            subnet = None
            if addr.netmask:
                try:
                    subnet = str(ipaddress.ip_network(f"{addr.address}/{addr.netmask}", strict=False))
                except ValueError:
                    subnet = None
            lower = name.lower()
            result.append(
                {
                    "name": name,
                    "ip": addr.address,
                    "subnet": subnet,
                    "zerotier": "zerotier" in lower,
                    "virtual": any(
                        x in lower
                        for x in ("vmware", "virtualbox", "vethernet", "hyper-v", "radmin", "tailscale")
                    ),
                }
            )
    return result


def _netsh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["netsh", "advfirewall", "firewall", *args],
        capture_output=True,
        text=True,
        errors="replace",
    )


def firewall_show() -> None:
    for name in LEGACY_RULE_NAMES:
        proc = _netsh("show", "rule", f"name={name}")
        text = (proc.stdout or "").strip()
        if proc.returncode == 0 and text and "没有" not in text:
            print(text)
            return
    print("（未找到本工具创建的防火墙规则）")


def firewall_delete() -> None:
    for name in LEGACY_RULE_NAMES:
        _netsh("delete", "rule", f"name={name}")


def firewall_apply(port: int, remoteip: str | None) -> bool:
    """添加/替换规则。remoteip 为 None 表示允许所有来源。"""
    if not is_admin():
        print("[错误] 修改防火墙需要管理员权限。")
        print("       请双击 network_config.bat（会自动请求 UAC），或右键以管理员身份运行。")
        return False
    firewall_delete()
    cmd = [
        "add",
        "rule",
        f"name={RULE_NAME}",
        "dir=in",
        "action=allow",
        "protocol=TCP",
        f"localport={port}",
        "profile=any",
    ]
    if remoteip:
        cmd.append(f"remoteip={remoteip}")
    proc = _netsh(*cmd)
    if proc.returncode == 0:
        scope = remoteip or "所有来源"
        print(f"[成功] 已放行 TCP {port}，来源：{scope}")
        return True
    print(f"[失败] {proc.stdout.strip() or proc.stderr.strip()}")
    return False


# ---------------------------------------------------------------------------
# 展示与菜单
# ---------------------------------------------------------------------------


def show() -> None:
    values = env_values()
    host = values.get("SERVER_HOST", "0.0.0.0")
    port = values.get("SERVER_PORT", "8000")
    print("=" * 68)
    print("实时双语字幕 — 网络配置")
    print("=" * 68)
    print(f"服务端监听 : {host}:{port}")
    print(f"配置文件   : {ENV_FILE}")
    print("-" * 68)
    print("本机网卡（手机需填写与手机同网段的地址）：")
    default_ip = default_route_ip()
    zt = zerotier_ip()
    for i, item in enumerate(interfaces(), 1):
        tags = []
        if item["ip"] == zt:
            tags.append("ZeroTier")
        if item["ip"] == default_ip:
            tags.append("默认路由")
        if item["ip"].startswith("169.254."):
            tags.append("未获取DHCP")
        if item["virtual"]:
            tags.append("虚拟网卡")
        suffix = f"  [{'/'.join(tags)}]" if tags else ""
        print(f"  {i}. {item['name']}")
        print(f"     {item['ip']}   子网 {item['subnet'] or '-'}{suffix}")
    print("-" * 68)
    print("当前防火墙规则：")
    firewall_show()
    print("-" * 68)
    print("提示：修改端口/监听地址后，需要重启服务端：")
    print("      stop_server.bat → start_server.bat")
    print("=" * 68)


def menu() -> int:
    while True:
        print(
            "\n"
            "1) 查看当前配置与网卡\n"
            "2) 修改服务端端口\n"
            "3) 修改监听地址（0.0.0.0 = 所有网卡）\n"
            "4) 配置防火墙放行范围\n"
            "5) 退出"
        )
        choice = input("请选择：").strip()

        if choice == "1":
            show()

        elif choice == "2":
            raw = input(f"新端口（当前 {env_values().get('SERVER_PORT', '8000')}）：").strip()
            if not raw.isdigit() or not (1 <= int(raw) <= 65535):
                print("端口应为 1~65535 的整数。")
                continue
            update_env("SERVER_PORT", raw)
            print(f"[成功] 端口已改为 {raw}。请重启服务端；并重新执行 4) 更新防火墙端口。")

        elif choice == "3":
            current = env_values().get("SERVER_HOST", "0.0.0.0")
            raw = input(f"新监听地址（当前 {current}，回车保持不变）：").strip() or current
            try:
                if raw != "0.0.0.0":
                    ipaddress.IPv4Address(raw)
            except ValueError:
                print("请输入合法的 IPv4 地址，或 0.0.0.0。")
                continue
            update_env("SERVER_HOST", raw)
            print(f"[成功] 监听地址已改为 {raw}。请重启服务端。")

        elif choice == "4":
            port = int(env_values().get("SERVER_PORT", "8000"))
            print(
                "\n"
                "1) 允许所有来源（临时演示最省事）\n"
                "2) 允许指定网段（从检测到的网卡选择）\n"
                "3) 仅允许 ZeroTier 网段\n"
                "4) 删除规则\n"
                "5) 返回"
            )
            sub = input("请选择：").strip()
            if sub == "1":
                firewall_apply(port, None)
            elif sub == "2":
                items = interfaces()
                default_ip = default_route_ip()
                subnets: list[tuple[str, bool]] = []
                for item in items:
                    if not item["subnet"] or item["ip"].startswith("169.254."):
                        continue
                    if any(item["subnet"] == existing for existing, _ in subnets):
                        continue
                    in_default = False
                    try:
                        in_default = ipaddress.ip_address(default_ip) in ipaddress.ip_network(
                            item["subnet"]
                        )
                    except ValueError:
                        pass
                    subnets.append((item["subnet"], in_default))
                subnets.sort(key=lambda x: (not x[1], x[0]))
                if not subnets:
                    print("未检测到可用网段。")
                    continue
                for i, (subnet, in_default) in enumerate(subnets, 1):
                    tag = "  [默认路由，通常与手机同网段]" if in_default else ""
                    print(f"  {i}) {subnet}{tag}")
                pick = input("选择网段编号：").strip()
                if not pick.isdigit() or not (1 <= int(pick) <= len(subnets)):
                    print("编号无效。")
                    continue
                firewall_apply(port, subnets[int(pick) - 1][0])
            elif sub == "3":
                firewall_apply(port, ZERO_TIER_SUBNET)
            elif sub == "4":
                if not is_admin():
                    print("[错误] 删除防火墙规则需要管理员权限。")
                else:
                    firewall_delete()
                    print("[成功] 已删除规则。")
            elif sub == "5":
                continue

        elif choice == "5":
            return 0
        else:
            print("无效选择。")


def main() -> int:
    ap = argparse.ArgumentParser(description="网络配置工具")
    ap.add_argument("--show", action="store_true", help="只显示当前配置")
    ap.add_argument("--firewall", choices=["any", "zerotier", "off"], help="设置防火墙范围")
    ap.add_argument("--set-port", type=int, help="修改服务端端口")
    ap.add_argument("--set-host", help="修改监听地址（0.0.0.0 表示所有网卡）")
    args = ap.parse_args()

    if args.show:
        show()
        return 0
    if args.set_port is not None:
        if not (1 <= args.set_port <= 65535):
            print("端口应为 1~65535。")
            return 1
        update_env("SERVER_PORT", str(args.set_port))
        print(f"[成功] 端口已改为 {args.set_port}。请重启服务端，并重新配置防火墙端口。")
        return 0
    if args.set_host:
        try:
            if args.set_host != "0.0.0.0":
                ipaddress.IPv4Address(args.set_host)
        except ValueError:
            print("请输入合法的 IPv4 地址，或 0.0.0.0。")
            return 1
        update_env("SERVER_HOST", args.set_host)
        print(f"[成功] 监听地址已改为 {args.set_host}。请重启服务端。")
        return 0
    if args.firewall:
        port = int(env_values().get("SERVER_PORT", "8000"))
        if args.firewall == "off":
            if not is_admin():
                print("[错误] 需要管理员权限。")
                return 1
            firewall_delete()
            print("[成功] 已删除防火墙规则。")
            return 0
        remote = None if args.firewall == "any" else ZERO_TIER_SUBNET
        return 0 if firewall_apply(port, remote) else 1

    return menu()


if __name__ == "__main__":
    raise SystemExit(main())
