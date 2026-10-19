"""FastAPI 入口（FR-18/FR-19/FR-20）。

启动流程：注册 CUDA DLL → 加载配置 → 加载 whisper 模型（失败即致命，FM-05）
→ 构建翻译服务 → 启动会话回收任务 → 挂载 ``/ws`` 与状态接口。

用法::

    .venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
    # 或在 server/ 目录下：python -m app.main
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse

from . import dll, protocol
from .asr import ASREngine, ModelUnavailableError
from .connection import SessionRegistry, handle_websocket
from .settings import APP_VERSION, Settings, get_settings
from .telemetry import get_logger, setup_logging
from .translate import TranslationService, build_translation_service

log = get_logger("main")


def create_app(
    settings: Settings | None = None,
    asr: ASREngine | None = None,
    translation: TranslationService | None = None,
    load_model: bool = True,
) -> FastAPI:
    """构建应用。

    ``asr`` / ``translation`` 可注入替身，便于测试在不加载模型与不联网的情况下
    验证协议与会话管道。
    """
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(settings.log_level)
        added = dll.register()
        if added:
            log.info("已注册 CUDA DLL 目录：%d 个", len(added))

        for warning in settings.runtime_warnings():
            log.warning("配置提示：%s", warning)

        app.state.settings = settings
        app.state.asr_engine = asr
        app.state.translation = translation

        # ---- 加载 ASR（FM-05：模型缺失为阻断项）----
        engine = asr
        if engine is None and load_model:
            engine = ASREngine(settings)
            try:
                await asyncio.to_thread(engine.load)
            except ModelUnavailableError as exc:
                log.critical("启动失败：%s", exc)
                raise
            app.state.asr_engine = engine

        # ---- 翻译服务 ----
        translation_service = translation or build_translation_service(settings)
        app.state.translation = translation_service
        if translation is None:
            # 预热翻译端点连接（不消耗 token），避免首句字幕承担 TLS 建连开销
            await translation_service.warm_up()

        registry = SessionRegistry(settings, engine, translation_service)
        app.state.registry = registry
        reaper = asyncio.create_task(registry.reap_loop())
        app.state.reaper = reaper

        log.info(
            "服务已就绪：%s:%d%s  设备=%s  翻译=%s",
            settings.server_host,
            settings.server_port,
            protocol.WS_PATH,
            getattr(engine, "device", "n/a"),
            "已配置" if settings.translate_configured else "未配置（降级）",
        )
        try:
            yield
        finally:
            reaper.cancel()
            try:
                await reaper
            except asyncio.CancelledError:
                pass
            await registry.close_all()
            await translation_service.aclose()
            log.info("服务已停止")

    app = FastAPI(title="实时双语字幕服务端", version=APP_VERSION, lifespan=lifespan)

    @app.websocket(protocol.WS_PATH)
    async def ws_endpoint(websocket: WebSocket) -> None:
        await handle_websocket(websocket)

    @app.get("/health")
    async def health() -> JSONResponse:
        engine: ASREngine | None = app.state.asr_engine
        return JSONResponse(
            {
                "status": "ok",
                "version": APP_VERSION,
                "protocol_version": protocol.PROTOCOL_VERSION,
                "asr": engine.status() if engine is not None else {"device": "stub"},
                "translate_configured": settings.translate_configured,
            }
        )

    @app.get("/status")
    async def status() -> JSONResponse:
        registry: SessionRegistry = app.state.registry
        return JSONResponse(
            {
                "version": APP_VERSION,
                "sessions": registry.active_count(),
                "latency": registry.latency.summary(),
                "cost": app.state.translation.cost.summary() if app.state.translation else {},
            }
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    _settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=_settings.server_host,
        port=_settings.server_port,
        log_level=_settings.log_level.lower(),
    )
