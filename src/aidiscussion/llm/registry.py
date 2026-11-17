"""模型接入的内存注册表。

聊天、摘要、记忆都只需要「厂商 id → 接入参数」这一层映射，
所以启动时把 providers 表加载成字典，工厂函数保持同步调用；
界面上增删改模型后再刷新一次即可。
"""

import threading

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.config import ProviderSpec, Settings, get_settings, provider_label
from aidiscussion.repositories import providers as provider_repo

_specs: dict[str, ProviderSpec] = {}
_lock = threading.Lock()


def _to_spec(row) -> ProviderSpec:
    return ProviderSpec(
        id=row.id,
        label=row.label,
        base_url=row.base_url,
        model=row.model,
        api_key=SecretStr(row.api_key) if row.api_key else None,
        send_temperature=row.send_temperature,
    )


async def refresh(session: AsyncSession) -> None:
    """从数据库重新加载；内置厂商之外的自定义模型也一并注册。"""
    rows = await provider_repo.list_providers(session)
    specs = {row.id: _to_spec(row) for row in rows}
    with _lock:
        _specs.clear()
        _specs.update(specs)


def clear() -> None:
    with _lock:
        _specs.clear()


def known(provider_id: str | None) -> bool:
    return (provider_id or "") in _specs


def spec_for(provider_id: str | None, settings: Settings | None = None) -> ProviderSpec:
    """取接入参数：优先数据库注册表，未注册时退回 .env 里的内置厂商。"""
    key = (provider_id or "").strip().lower()
    with _lock:
        spec = _specs.get(key)
    if spec is not None:
        return spec
    return (settings or get_settings()).provider_spec(key or None)


def label_for(provider_id: str | None) -> str:
    key = (provider_id or "").strip().lower()
    with _lock:
        spec = _specs.get(key)
    return spec.label if spec is not None else provider_label(key)
