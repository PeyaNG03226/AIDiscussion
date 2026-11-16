"""长期记忆仓储。"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.db.models import Memory


async def list_memories(session: AsyncSession, *, limit: int = 200) -> list[Memory]:
    stmt = select(Memory).order_by(Memory.id.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def find_by_content(session: AsyncSession, content: str) -> Memory | None:
    stmt = select(Memory).where(Memory.content == content)
    return (await session.execute(stmt)).scalars().first()


async def add_memory(
    session: AsyncSession,
    *,
    content: str,
    source_conversation_id: str | None = None,
) -> Memory:
    memory = Memory(
        content=content.strip(),
        source_conversation_id=source_conversation_id,
    )
    session.add(memory)
    await session.flush()
    return memory


async def search_candidates(
    session: AsyncSession, *, limit: int = 500
) -> Sequence[Memory]:
    """召回阶段先把候选集控制在合理规模，再做关键词打分。"""
    stmt = select(Memory).order_by(Memory.id.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())
