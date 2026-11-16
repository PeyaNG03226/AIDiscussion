"""应用配置：统一从环境变量与 .env 读取，代码中不允许出现硬编码密钥。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_timeout: float = 60.0
    llm_max_retries: int = 2
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # 上下文预算：超过后把更早的消息压缩成摘要
    context_max_tokens: int = 2400
    context_keep_recent: int = 12

    # 长期记忆：从用户消息里提取跨会话事实，并在组装上下文时召回
    memory_enabled: bool = True
    memory_top_k: int = 5

    @property
    def has_llm_key(self) -> bool:
        if self.llm_api_key is None:
            return False
        return bool(self.llm_api_key.get_secret_value().strip())

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
