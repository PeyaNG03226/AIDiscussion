"""应用配置：统一从环境变量与 .env 读取，代码中不允许出现硬编码密钥。"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# 支持的厂商：id 存进数据库，label 给界面显示
PROVIDER_LABELS: dict[str, str] = {
    "doubao": "豆包",
    "kimi": "Kimi",
    "deepseek": "DeepSeek",
    "qwen": "通义千问",
}

DEFAULT_PROVIDER = "deepseek"


def normalize_provider(value: str | None) -> str:
    """把任意写法收敛成受支持的厂商 id，未知值退回默认厂商。"""
    provider = (value or "").strip().lower()
    return provider if provider in PROVIDER_LABELS else DEFAULT_PROVIDER


def provider_label(value: str | None) -> str:
    provider = normalize_provider(value)
    return PROVIDER_LABELS[provider]


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """一个厂商的接入参数：成员只存 provider id，密钥来自环境变量。"""

    id: str
    label: str
    base_url: str
    model: str
    api_key: SecretStr | None
    # Kimi 的 k 系列只接受 temperature=1，传别的值会 400，所以这些厂商不发 temperature
    send_temperature: bool = True

    @property
    def has_key(self) -> bool:
        if self.api_key is None:
            return False
        return bool(self.api_key.get_secret_value().strip())


class Settings(BaseSettings):
    """全局配置。

    环境变量统一使用 AIDISCUSSION_ 前缀，例如 AIDISCUSSION_LLM_API_KEY。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AIDISCUSSION_",
        extra="ignore",
    )

    app_name: str = "AIDiscussion"
    debug: bool = False
    log_level: str = "INFO"
    data_dir: Path = Field(default=Path("./data"))

    # 留空则自动使用 data_dir 下的 SQLite 文件
    database_url: str | None = None

    # 模型接入：任何 OpenAI 兼容接口都可用
    # 下面这组是「默认厂商」的旧配置，仍然生效；多厂商请填各自的 AIDISCUSSION_<厂商>_API_KEY
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_timeout: float = 60.0
    llm_max_retries: int = 2
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # 多厂商：每个 AI 成员绑定一个厂商，密钥互不干扰
    # 模型名各账号权限不同，报「模型不存在」时用对应厂商的 /v1/models 查可用 id，
    # 或直接改 .env 里的 AIDISCUSSION_<厂商>_MODEL
    doubao_api_key: SecretStr | None = None
    doubao_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    doubao_model: str = "doubao-seed-character-251128"

    kimi_api_key: SecretStr | None = None
    kimi_base_url: str = "https://api.moonshot.cn/v1"
    kimi_model: str = "kimi-k2.6"

    # DeepSeek 留空则复用上面的 llm_* 配置，兼容旧 .env
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str | None = None
    deepseek_model: str | None = None

    qwen_api_key: SecretStr | None = None
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen3.8-flash"

    # 上下文预算：超过后把更早的消息压缩成摘要
    context_max_tokens: int = 2400
    context_keep_recent: int = 12

    # 长期记忆：从用户消息里提取跨会话事实，并在组装上下文时召回
    memory_enabled: bool = True
    memory_top_k: int = 5
    # 跨会话背景：私聊时也能看到群里刚聊什么（只注入最近几条，控制 token）
    cross_conversation_context: bool = True
    cross_context_messages: int = 8

    @property
    def has_llm_key(self) -> bool:
        if self.llm_api_key is None:
            return False
        return bool(self.llm_api_key.get_secret_value().strip())

    def provider_spec(self, provider: str | None) -> ProviderSpec:
        """取某个厂商的接入参数；未配置密钥时 has_key 为 False。"""
        key = normalize_provider(provider)
        if key == "doubao":
            api_key, base_url, model = self.doubao_api_key, self.doubao_base_url, self.doubao_model
        elif key == "kimi":
            api_key, base_url, model = self.kimi_api_key, self.kimi_base_url, self.kimi_model
        elif key == "qwen":
            api_key, base_url, model = self.qwen_api_key, self.qwen_base_url, self.qwen_model
        else:
            api_key = self.deepseek_api_key or self.llm_api_key
            base_url = self.deepseek_base_url or self.llm_base_url
            model = self.deepseek_model or self.llm_model
        return ProviderSpec(
            id=key,
            label=PROVIDER_LABELS[key],
            base_url=base_url,
            model=model,
            api_key=api_key,
            send_temperature=key != "kimi",
        )

    def provider_specs(self) -> list[ProviderSpec]:
        return [self.provider_spec(key) for key in PROVIDER_LABELS]

    def masked_llm_key(self) -> str:
        """用于日志和自检输出，禁止打印完整密钥。"""
        if not self.has_llm_key:
            return "<未配置>"
        value = self.llm_api_key.get_secret_value()  # type: ignore[union-attr]
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}...{value[-4:]}"

    def ensure_data_dir(self) -> Path:
        """确保数据目录存在，返回该目录。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        db_file = (self.data_dir / "aidiscussion.db").absolute()
        return f"sqlite+aiosqlite:///{db_file.as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
