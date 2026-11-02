"""服务端配置：从 ``server/.env`` 与环境变量加载并校验。

配置项定义见《03-接口与通信协议设计》§9.4。本模块只负责**读取与校验**，
不打印任何密钥内容（NFR-07）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_DIR = Path(__file__).resolve().parent.parent  # …/server
PROJECT_ROOT = SERVER_DIR.parent  # 仓库根
ENV_FILE = SERVER_DIR / ".env"

APP_VERSION = "0.2.0"
PROTOCOL_VERSION = 1


class Settings(BaseSettings):
    """服务端运行配置。字段名与 .env 中的大写键名一一对应（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- 服务监听 ----------
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # ---------- 语音识别（faster-whisper）----------
    whisper_model: str = "medium"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    # GPU 不可用时降级为 CPU 推理所用精度（FM-04）
    whisper_cpu_compute_type: str = "int8"

    # ---------- 断句（VAD）----------
    vad_min_silence_ms: int = 500
    vad_min_speech_ms: int = 300
    vad_max_sentence_ms: int = 8000
    vad_threshold: float = 0.5
    vad_neg_threshold: float | None = None  # 留空则按 threshold-0.15 推导
    vad_pre_roll_ms: int = 200

    # ---------- 翻译后端（E2：deepseek 云端 / ollama 本地）----------
    translate_backend: str = "deepseek"

    # ---------- 翻译（OpenAI 兼容接口，默认 DeepSeek）----------
    translate_base_url: str = "https://api.deepseek.com"
    translate_api_key: str = ""
    translate_model: str = "deepseek-chat"
    translate_timeout_s: float = 10.0
    translate_max_retry: int = 2
    translate_concurrency: int = 3

    # ---------- 本地模型翻译（E2：Ollama，OpenAI 兼容端点）----------
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    # 本地模型首次调用要加载权重，给更宽的超时预算
    ollama_timeout_s: float = 30.0

    # ---------- 接入鉴权与传输（E1：跨网络/公网）----------
    # 非空时，/ws 要求 ?token=… 或 Authorization: Bearer …；留空表示不鉴权（仅限可信局域网）
    auth_token: str = ""
    # 配置后由服务端直接终结 TLS（wss://）；只用 frp/nginx 终结时可留空
    ssl_certfile: str = ""
    ssl_keyfile: str = ""

    # ---------- 会话 ----------
    session_ttl_s: int = 30
    max_queue: int = 10

    # ---------- 字幕历史（可选，本地 JSONL）----------
    # 开启后每句字幕写入 SUBTITLE_HISTORY_PATH（含原文/译文，文件已被 .gitignore 排除）。
    # 默认关闭：字幕内容属于用户数据，是否落盘由部署者决定（NFR-11）。
    subtitle_history: bool = False
    subtitle_history_path: str = "logs/subtitles.jsonl"

    # ---------- 成本监控（FR-21 / NFR-10）----------
    cost_notify_yuan: float = 10.0
    cost_notify_tokens: int = 1_000_000_000
    # DeepSeek 参考价（元 / 百万 token），部署者可按实际计费修改
    price_in_per_m: float = 2.0
    price_out_per_m: float = 8.0

    # ---------- 日志 ----------
    log_level: str = "INFO"

    # ------------------------------------------------------------------
    @field_validator("vad_neg_threshold", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v

    @field_validator("translate_backend", mode="before")
    @classmethod
    def _check_backend(cls, v: object) -> object:
        value = str(v or "deepseek").strip().lower()
        if value not in ("deepseek", "ollama"):
            raise ValueError("TRANSLATE_BACKEND 只支持 deepseek / ollama")
        return value

    @property
    def translate_is_local(self) -> bool:
        """翻译后端是否为本地模型（E2：本地推理，无计费、数据不出内网）。"""
        return self.translate_backend == "ollama"

    @property
    def translate_model_name(self) -> str:
        """当前后端所用的模型名（/health 展示用，不含任何密钥）。"""
        return self.ollama_model if self.translate_is_local else self.translate_model

    @property
    def translate_configured(self) -> bool:
        """翻译后端是否可用（不返回、不打印密钥本身）。

        本地 Ollama 不需要密钥，只要选中该后端即视为可用；云端后端校验密钥是否已填。
        """
        if self.translate_is_local:
            return True
        key = (self.translate_api_key or "").strip()
        return bool(key) and not key.startswith("sk-在此")

    @property
    def auth_required(self) -> bool:
        """是否启用 /ws 鉴权（E1）。"""
        return bool((self.auth_token or "").strip())

    @property
    def tls_enabled(self) -> bool:
        """是否由服务端直接终结 TLS（wss）。"""
        return bool(self.ssl_certfile.strip() and self.ssl_keyfile.strip())

    @property
    def scheme(self) -> str:
        return "wss" if self.tls_enabled else "ws"

    @property
    def api_key_length(self) -> int:
        return len((self.translate_api_key or "").strip())

    @property
    def whisper_model_path(self) -> str:
        """模型名可能是规格名（medium），也可能是相对项目根目录的本地路径。"""
        candidate = PROJECT_ROOT / self.whisper_model
        if (candidate / "model.bin").is_file():
            return str(candidate)
        return self.whisper_model

    @property
    def subtitle_history_file(self) -> Path:
        """字幕历史文件路径；相对路径按项目根目录解析。"""
        path = Path(self.subtitle_history_path).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path

    def runtime_warnings(self) -> list[str]:
        """返回不影响启动、但部署者应知晓的配置提示。"""
        warnings: list[str] = []
        if not self.translate_configured:
            warnings.append(
                "TRANSLATE_API_KEY 未填写，翻译将直接降级回传原文（FM-02）。"
            )
        if self.translate_is_local:
            warnings.append(
                f"翻译后端为本地 Ollama（{self.ollama_model} @ {self.ollama_base_url}）："
                "音频与文本均不出内网（E2），不产生 API 费用；"
                "请确认 ollama 已启动且已 pull 该模型（scripts/verify_ollama.py 可自检）。"
            )
        if self.auth_required:
            warnings.append(
                "已启用 AUTH_TOKEN 鉴权：客户端连接 /ws 时必须带 ?token=… 或 "
                "Authorization: Bearer …。令牌属于密钥，请勿写入仓库或日志。"
            )
        if self.auth_required and not self.tls_enabled:
            warnings.append(
                "已启用鉴权但未配置 TLS 证书：若要把端口暴露到公网，"
                "请由 frp/nginx 终结 wss，或配置 SSL_CERTFILE/SSL_KEYFILE。"
            )
        if not (1 <= self.server_port <= 65535):
            warnings.append(f"SERVER_PORT={self.server_port} 超出合法范围。")
        if self.vad_max_sentence_ms <= self.vad_min_silence_ms:
            warnings.append("VAD_MAX_SENTENCE_MS 应大于 VAD_MIN_SILENCE_MS。")
        if self.whisper_device == "cpu":
            warnings.append(
                "WHISPER_DEVICE=cpu：延迟可能不满足 NFR-01 的 800ms/句预算。"
            )
        if self.subtitle_history:
            warnings.append(
                f"SUBTITLE_HISTORY=true：原文与译文将写入 {self.subtitle_history_file}"
                "（本地文件，已 gitignore；不需要时设为 false）。"
            )
        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
