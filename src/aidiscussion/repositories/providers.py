"""模型接入配置的仓储：内置厂商播种 + 自定义模型的增删改查。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.config import ProviderSpec
from aidiscussion.db.models import Member, Provider, new_id


async def list_providers(session: AsyncSession) -> list[Provider]:
    result = await session.execute(
        select(Provider).order_by(Provider.is_builtin.desc(), Provider.created_at)
    )
    return list(result.scalars())


async def get_provider(session: AsyncSession, provider_id: str) -> Provider | None:
    return await session.get(Provider, provider_id)


async def create_provider(
    session: AsyncSession,
    *,
    label: str,
    base_url: str,
    model: str,
    api_key: str = "",
    send_temperature: bool = True,
    is_builtin: bool = False,
) -> Provider:
    provider = Provider(
        id=new_id()[:12],
        label=label,
        base_url=base_url,
        model=model,
        api_key=api_key,
        send_temperature=send_temperature,
        is_builtin=is_builtin,
    )
    session.add(provider)
    await session.flush()
    return provider


async def delete_provider(session: AsyncSession, provider: Provider) -> None:
    await session.delete(provider)
    await session.flush()


async def count_members(session: AsyncSession, provider_id: str) -> int:
    """还有多少成员绑定在这个模型上，删除前用来拦截。"""
    result = await session.execute(
        select(func.count()).select_from(Member).where(Member.provider == provider_id)
    )
    return int(result.scalar_one())


async def seed_builtin(session: AsyncSession, specs: list[ProviderSpec]) -> None:
    """首次启动时按 .env 播种内置厂商。

    只在表为空时执行：之后一切以数据库为准，界面上删掉的内置厂商
    不会因为重启又被塞回来。
    """
    existing = await session.scalar(select(func.count()).select_from(Provider))
    if existing:
        return

    for spec in specs:
        session.add(
            Provider(
                id=spec.id,
                label=spec.label,
                base_url=spec.base_url,
                model=spec.model,
                api_key=spec.api_key.get_secret_value() if spec.api_key else "",
                send_temperature=spec.send_temperature,
                is_builtin=True,
            )
        )
    await session.flush()
