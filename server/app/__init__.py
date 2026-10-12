"""实时双语字幕系统 — 服务端应用包。

模块划分见《02-系统架构设计》§4.2 与 §12：
    settings   配置加载与校验
    protocol   协议常量、音频帧编解码、消息构造
    telemetry  日志与各阶段耗时统计
    dll        Windows 下 CUDA 运行时 DLL 注册（须在导入推理库之前调用）
    buffer     音频帧校验与丢帧统计
    segmenter  Silero VAD 流式断句
    asr        faster-whisper 封装（串行队列 + 降级）
    translate  Translator 抽象 + DeepSeek 实现 + 成本监控
    session    会话状态与端到端管道
    connection WebSocket 接入
    main       FastAPI 入口
"""

__all__ = ["APP_VERSION"]

APP_VERSION = "0.1.0"
