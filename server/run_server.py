"""服务端启动入口（供 `start_server.bat` 或命令行使用）。

用法::

    .venv/Scripts/python.exe server/run_server.py

配置来自 `server/.env`（SERVER_HOST / SERVER_PORT / WHISPER_* / TRANSLATE_* 等）。
启动前检查端口占用，并打印手机端应填写的主机地址（优先 ZeroTier）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import uvicorn  # noqa: E402

from app.main import app  # noqa: E402
from app.settings import APP_VERSION, get_settings  # noqa: E402
from netutil import ipv4_candidates, port_in_use, port_owner, zerotier_ip  # noqa: E402


def main() -> int:
    settings = get_settings()
    port = settings.server_port

    # ---- 端口占用预检：给出可操作提示，而不是 uvicorn 的原始 traceback ----
    if port_in_use(settings.server_host, port):
        owner = port_owner(port)
        print("=" * 66)
        print(f"[错误] 端口 {port} 已被占用，无法启动第二个服务端。")
        if owner:
            pid, name, exe = owner
            print(f"占用进程 : PID {pid}  {name}")
            print(f"路径     : {exe}")
        print()
        print("可能上次启动的服务端仍在运行。可选处理：")
        print("  1) 直接使用它：浏览器打开 http://<本机IP>:%d/health 验证" % port)
        if os.name == "nt":
            print("  2) 停止它：双击 stop_server.bat（仅会结束占用端口的 Python 进程）")
            if owner:
                print(f"     或手动：taskkill /PID {owner[0]} /F")
        else:
            print("  2) 停止它：./stop_server.sh（仅会结束占用端口的 Python 进程）")
            if owner:
                print(f"     或手动：kill {owner[0]}")
        print(f"  3) 换端口：在 server/.env 设置 SERVER_PORT=8001 后重试")
        print("=" * 66)
        return 2

    candidates = ipv4_candidates()
    zt = zerotier_ip()
    primary = candidates[0] if candidates else "127.0.0.1"
    print("=" * 66)
    print(f"实时双语字幕服务端  v{APP_VERSION}")
    print("=" * 66)
    print(f"监听地址   : {settings.server_host}:{port}")
    hint = "ZeroTier 虚拟局域网" if zt and primary == zt else "默认路由出口"
    print(f"手机端填写 : {primary}:{port}   （{hint}）")
    if some := candidates[1:]:
        print(f"其他候选   : {', '.join(some)}")
    if zt:
        print(f"ZeroTier   : {zt}（手机需安装 ZeroTier One 并加入同一网络）")
    print(
        f"             若手机连不上：用浏览器访问 "
        f"http://{primary}:{port}/health 自检；"
    )
    print("             并以管理员身份运行 allow_firewall.bat 放行 8000/TCP（仅 ZeroTier 网段）。")
    print(f"识别设备   : {settings.whisper_device} / {settings.whisper_compute_type}")
    if settings.translate_is_local:
        print(
            f"翻译后端   : 本地 Ollama（{settings.ollama_model} @ {settings.ollama_base_url}）"
            "  —— 音频与文本不出内网（E2）"
        )
    else:
        print(
            f"翻译后端   : {settings.translate_base_url} / {settings.translate_model} "
            f"({'已配置密钥' if settings.translate_configured else '未配置，将降级为原文'})"
        )
    if settings.auth_required:
        print(
            f"接入鉴权   : 已启用（{settings.scheme}），客户端需带 ?token=… 或 "
            "Authorization: Bearer …"
        )
        if not settings.tls_enabled:
            print("             提示：未配置 TLS 证书，跨公网请由 frp/nginx 终结 wss。")
    else:
        print("接入鉴权   : 关闭（仅建议用于可信局域网）")
    print("状态接口   : /health 、/status 、/latency（逐句明细）")
    print("首次启动需加载 whisper 模型（约 10~30 秒）…")
    print("按 Ctrl+C 停止。")
    print("=" * 66)
    if primary == "127.0.0.1":
        print("提示：未获取到可用 IPv4，请确认已连接 Wi-Fi/网线。")
    print("提示：若手机连不上，请检查 Windows 防火墙是否放行 Python 的端口。")
    print()

    ssl_kwargs = {}
    if settings.tls_enabled:
        # E1：服务端直接终结 TLS，客户端用 wss://（也可交给 frp/nginx 终结）
        ssl_kwargs = {
            "ssl_certfile": settings.ssl_certfile,
            "ssl_keyfile": settings.ssl_keyfile,
        }
    uvicorn.run(
        app,
        host=settings.server_host,
        port=port,
        log_level=settings.log_level.lower(),
        **ssl_kwargs,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
