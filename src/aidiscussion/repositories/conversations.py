"""会话仓储：群聊与私聊共用。"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.db.models import (
    Conversation,
    ConversationMember,
    ConversationType,
    Member,
    utcnow,
)


async def create_conversation(
    session: AsyncSession,
    *,
    type: ConversationType,
    title: str = "",
    member_ids: Sequence[str],
) -> Conversation:
    unique_ids = list(dict.fromkeys(member_ids))
    if not unique_ids:
        raise ValueError("会话至少需要一个成员")

    member_stmt = select(Member).where(Member.id.in_(unique_ids), Member.is_active.is_(True))
    found_members = {
        member.id: member for member in (await session.execute(member_stmt)).scalars().all()
    }
    missing = [member_id for member_id in unique_ids if member_id not in found_members]
    if missing:
        raise ValueError(f"成员不存在或已停用：{', '.join(missing)}")

    conversation = Conversation(type=type, title=title)
    conversation.member_links = [
        ConversationMember(member_id=member_id, member=found_members[member_id])
        for member_id in unique_ids
    ]
    session.add(conversation)
    await session.flush()
    return conversation


async def get_conversation(session: AsyncSession, conversation_id: str) -> Conversation | None:
    return await session.get(Conversation, conversation_id)


async def list_conversations(session: AsyncSession, *, limit: int = 100) -> list[Conversation]:
    """按最近活跃排序：有消息用最后消息时间，否则用创建时间。"""
    activity = func.coalesce(Conversation.last_message_at, Conversation.created_at)
    stmt = select(Conversation).order_by(activity.desc(), Conversation.id).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def add_member(session: AsyncSession, conversation_id: str, member_id: str) -> bool:
    """把成员拉进会话；已存在或成员不可用时返回 False。"""
    conversation = await get_conversation(session, conversation_id)
    if conversation is None:
        raise ValueError(f"会话不存在：{conversation_id}")

    member = await session.get(Member, member_id)
    if member is None or not member.is_active:
        return False

    if any(link.member_id == member_id for link in conversation.member_links):
        return False

    session.add(
        ConversationMember(conversation_id=conversation_id, member_id=member_id, member=member)
    )
    await session.flush()
    await session.refresh(conversation, ["member_links"])
    return True


async def remove_member(session: AsyncSession, conversation_id: str, member_id: str) -> bool:
    """踢出会话。历史消息保留，成员本人不会被删除。"""
    conversation = await get_conversation(session, conversation_id)
    if conversation is None:
        raise ValueError(f"会话不存在：{conversation_id}")

    link = next((item for item in conversation.member_links if item.member_id == member_id), None)
    if link is None:
        return False

    await session.delete(link)
    await session.flush()
    await session.refresh(conversation, ["member_links"])
    return True


async def get_direct_conversation(
    session: AsyncSession, member_a: str, member_b: str
) -> Conversation | None:
    """查找两个成员之间已有的私聊，避免重复建会话。"""
    if member_a == member_b:
        raise ValueError("私聊双方不能是同一个成员")

    pair = select(ConversationMember.conversation_id).where(
        ConversationMember.member_id.in_([member_a, member_b])
    )
    stmt = (
        select(Conversation)
        .where(Conversation.type == ConversationType.DIRECT)
        .where(Conversation.id.in_(pair))
        .where(
            Conversation.id.in_(
                select(ConversationMember.conversation_id)
                .group_by(ConversationMember.conversation_id)
                .having(func.count(ConversationMember.member_id) == 2)
            )
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def touch_conversation(
    session: AsyncSession, conversation_id: str, *, at: datetime | None = None
) -> None:
    """写入新消息后更新会话活跃时间，驱动左侧列表排序。"""
    conversation = await get_conversation(session, conversation_id)
    if conversation is None:
        raise ValueError(f"会话不存在：{conversation_id}")
    conversation.last_message_at = at or utcnow()
    await session.flush()
