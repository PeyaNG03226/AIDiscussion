"""会话摘要仓储。"""

from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.db.models import ConversationSummary


async def get_summary(
    session: AsyncSession, conversation_id: str
) -> ConversationSummary | None:
    return await session.get(ConversationSummary, conversation_id)


async def save_summary(
    session: AsyncSession,
    *,
    conversation_id: str,
    content: str,
    up_to_message_id: int,
) -> ConversationSummary:
    row = await session.get(ConversationSummary, conversation_id)
    if row is None:
        row = ConversationSummary(conversation_id=conversation_id)
        session.add(row)
    row.content = content
    row.up_to_message_id = up_to_message_id
    await session.flush()
    return row
