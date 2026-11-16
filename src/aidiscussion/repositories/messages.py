"""消息仓储。"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from aidiscussion.db.models import Message, MessageStatus
from aidiscussion.repositories.conversations import touch_conversation


async def add_message(
    session: AsyncSession,
    *,
    conversation_id: str,
    sender_id: str,
    content: str,
    status: MessageStatus = MessageStatus.SENT,
    touch: bool = True,
) -> Message:
    if not content.strip():
        raise ValueError("消息内容不能为空")

    message = Message(
        conversation_id=conversation_id,
        sender_id=sender_id,
        content=content,
        status=status,
    )
    session.add(message)
    await session.flush()

    if touch:
        await touch_conversation(session, conversation_id, at=message.created_at)
    return message


async def list_messages(
    session: AsyncSession,
    conversation_id: str,
    *,
    limit: int = 50,
    before_id: int | None = None,
) -> list[Message]:
    """返回按时间正序的消息，before_id 用于向上翻页。"""
    stmt = (
        select(Message)
        .options(joinedload(Message.sender))
        .where(Message.conversation_id == conversation_id)
    )
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)
    stmt = stmt.order_by(Message.id.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return list(reversed(rows))


async def count_messages(session: AsyncSession, conversation_id: str) -> int:
    stmt = select(Message.id).where(Message.conversation_id == conversation_id)
    return len((await session.execute(stmt)).scalars().all())


async def recent_messages(
    session: AsyncSession,
    conversation_id: str,
    *,
    limit: int = 20,
    exclude_ids: Sequence[int] = (),
) -> list[Message]:
    """取最近若干条消息，供组装模型上下文使用。"""
    stmt = (
        select(Message)
        .options(joinedload(Message.sender))
        .where(Message.conversation_id == conversation_id)
    )
    if exclude_ids:
        stmt = stmt.where(Message.id.not_in(exclude_ids))
    stmt = stmt.order_by(Message.id.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return list(reversed(rows))
