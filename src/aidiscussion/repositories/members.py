"""成员仓储：AI 成员和你自己（human）都存放在 members 表。"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.db.models import Member, MemberKind

_EDITABLE_FIELDS = {"name", "avatar", "model", "system_prompt", "temperature", "is_active"}


async def create_member(
    session: AsyncSession,
    *,
    name: str,
    kind: MemberKind = MemberKind.AI,
    avatar: str = "",
    model: str | None = None,
    system_prompt: str = "",
    temperature: float = 0.7,
) -> Member:
    """创建成员。

    同名成员曾被停用时直接复用并刷新配置，避免「删除后无法用同名重建」。
    """
    name = name.strip()
    if not name:
        raise ValueError("成员名称不能为空")

    existing = await get_member_by_name(session, name)
    if existing is not None:
        if existing.is_active:
            raise ValueError(f"成员名称已存在：{name}")
        existing.kind = kind
        existing.avatar = avatar
        existing.model = model
        existing.system_prompt = system_prompt
        existing.temperature = temperature
        existing.is_active = True
        await session.flush()
        return existing

    member = Member(
        name=name,
        kind=kind,
        avatar=avatar,
        model=model,
        system_prompt=system_prompt,
        temperature=temperature,
    )
    session.add(member)
    await session.flush()
    return member


async def get_member(session: AsyncSession, member_id: str) -> Member | None:
    return await session.get(Member, member_id)


async def get_member_by_name(session: AsyncSession, name: str) -> Member | None:
    stmt = select(Member).where(Member.name == name)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_members(
    session: AsyncSession,
    *,
    kind: MemberKind | None = None,
    include_inactive: bool = False,
) -> list[Member]:
    stmt = select(Member)
    if kind is not None:
        stmt = stmt.where(Member.kind == kind)
    if not include_inactive:
        stmt = stmt.where(Member.is_active.is_(True))
    stmt = stmt.order_by(Member.created_at, Member.name)
    return list((await session.execute(stmt)).scalars().all())


async def update_member(session: AsyncSession, member_id: str, **changes: Any) -> Member:
    unknown = set(changes) - _EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"不允许修改的字段：{', '.join(sorted(unknown))}")

    member = await get_member(session, member_id)
    if member is None:
        raise ValueError(f"成员不存在：{member_id}")

    for field, value in changes.items():
        setattr(member, field, value)
    await session.flush()
    return member


async def deactivate_member(session: AsyncSession, member_id: str) -> bool:
    """停用成员而不是物理删除，保证历史消息的发送者信息仍然可读。"""
    member = await get_member(session, member_id)
    if member is None or not member.is_active:
        return False
    member.is_active = False
    await session.flush()
    return True


async def get_self_member(session: AsyncSession, *, name: str = "我") -> Member:
    """取当前用户（human），不存在则创建。"""
    stmt = (
        select(Member)
        .where(Member.kind == MemberKind.HUMAN)
        .order_by(Member.created_at)
        .limit(1)
    )
    member = (await session.execute(stmt)).scalars().first()
    if member is not None:
        return member
    return await create_member(session, name=name, kind=MemberKind.HUMAN)
