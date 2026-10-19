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

APP_VERSION = "0.1.0"
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

    # ---------- 翻译（OpenAI 兼容接口，默认 DeepSeek）----------
    translate_base_url: str = "https://api.deepseek.com"
    translate_api_key: str = ""
    translate_model: str = "deepseek-chat"
    translate_timeout_s: float = 10.0
    translate_max_retry: int = 2
    translate_concurrency: int = 3

    # ---------- 会话 ----------
    session_ttl_s: int = 30
    max_queue: int = 10

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

    @property
    def translate_configured(self) -> bool:
        """密钥是否已填写（不返回、不打印密钥本身）。"""
        key = (self.translate_api_key or "").strip()
        return bool(key) and not key.startswith("sk-在此")

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

    def runtime_warnings(self) -> list[str]:
        """返回不影响启动、但部署者应知晓的配置提示。"""
        warnings: list[str] = []
        if not self.translate_configured:
            warnings.append(
                "TRANSLATE_API_KEY 未填写，翻译将直接降级回传原文（FM-02）。"
            )
        if not (1 <= self.server_port <= 65535):
            warnings.append(f"SERVER_PORT={self.server_port} 超出合法范围。")
        if self.vad_max_sentence_ms <= self.vad_min_silence_ms:
            warnings.append("VAD_MAX_SENTENCE_MS 应大于 VAD_MIN_SILENCE_MS。")
        if self.whisper_device == "cpu":
            warnings.append(
                "WHISPER_DEVICE=cpu：延迟可能不满足 NFR-01 的 800ms/句预算。"
            )
        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
