"""根据配置创建模型客户端。"""

import logging
from functools import lru_cache

import httpx

from aidiscussion.config import Settings, get_settings
from aidiscussion.llm.base import LLMClient
from aidiscussion.llm.mock import MockLLM
from aidiscussion.llm.openai_compatible import OpenAICompatibleClient

logger = logging.getLogger(__name__)


def build_llm(settings: Settings, *, client: httpx.AsyncClient | None = None) -> LLMClient:
    if not settings.has_llm_key:
        logger.warning("未配置 AIDISCUSSION_LLM_API_KEY，使用离线 Mock 模型")
        return MockLLM()

    api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
    return OpenAICompatibleClient(
        api_key=api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout=settings.llm_timeout,
        max_retries=settings.llm_max_retries,
        client=client,
    )


@lru_cache
def get_llm() -> LLMClient:
    """进程内单例，复用 HTTP 连接池。"""
    return build_llm(get_settings())
