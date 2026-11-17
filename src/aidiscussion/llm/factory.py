"""根据配置创建模型客户端。

每个厂商一个客户端：成员表里只存 provider id，接入参数来自 providers 表
（启动时按 .env 播种内置厂商，界面里也可以新增任意 OpenAI 兼容服务），
因此同一群里可以同时挂不同厂家、不同部署的模型。
"""

import logging

import httpx

from aidiscussion.config import ProviderSpec, Settings, get_settings
from aidiscussion.llm import registry
from aidiscussion.llm.base import LLMClient
from aidiscussion.llm.mock import MockLLM
from aidiscussion.llm.openai_compatible import OpenAICompatibleClient

logger = logging.getLogger(__name__)

_clients: dict[str, LLMClient] = {}


def build_llm_for(
    spec: ProviderSpec, settings: Settings | None = None, *, client: httpx.AsyncClient | None = None
) -> LLMClient:
    """按厂商参数建客户端；该厂商没配密钥时退回离线 Mock，保证界面仍可用。"""
    current = settings or get_settings()
    if not spec.has_key:
        logger.warning("未配置 %s 的 API Key，该厂商的成员使用离线 Mock 模型", spec.label)
        return MockLLM()

    api_key = spec.api_key.get_secret_value() if spec.api_key else ""
    return OpenAICompatibleClient(
        api_key=api_key,
        base_url=spec.base_url,
        model=spec.model,
        timeout=current.llm_timeout,
        max_retries=current.llm_max_retries,
        send_temperature=spec.send_temperature,
        client=client,
    )


def get_llm_for(provider: str | None, settings: Settings | None = None) -> LLMClient:
    """按厂商取单例客户端，复用 HTTP 连接池。"""
    current = settings or get_settings()
    spec = registry.spec_for(provider, current)
    cached = _clients.get(spec.id)
    if cached is None:
        cached = build_llm_for(spec, current)
        _clients[spec.id] = cached
    return cached


async def invalidate(provider: str) -> None:
    """模型配置被改动后丢弃旧客户端，下次发言用新参数重建。"""
    cached = _clients.pop((provider or "").strip().lower(), None)
    if cached is not None:
        await cached.aclose()


def get_llm(settings: Settings | None = None) -> LLMClient:
    """默认厂商客户端：摘要、记忆提取、主持人选择都用它。"""
    current = settings or get_settings()
    return get_llm_for(current.provider_spec(None).id, current)


async def aclose_llm_clients() -> None:
    for client in _clients.values():
        await client.aclose()
    _clients.clear()


__all__ = [
    "aclose_llm_clients",
    "build_llm_for",
    "get_llm",
    "get_llm_for",
    "invalidate",
]
