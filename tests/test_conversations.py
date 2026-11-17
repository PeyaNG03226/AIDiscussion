"""会话仓储测试：群聊与私聊共用一套表。"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.db.models import ConversationType
from aidiscussion.repositories import conversations as conv_repo
from aidiscussion.repositories import members as member_repo
from aidiscussion.repositories import messages as msg_repo


async def make_members(session: AsyncSession, *names: str) -> list[str]:
    created = [await member_repo.create_member(session, name=name) for name in names]
    return [member.id for member in created]


async def test_create_group_with_members(session: AsyncSession) -> None:
    me = await member_repo.get_self_member(session)
    ai_ids = await make_members(session, "地产顾问", "法务")

    conversation = await conv_repo.create_conversation(
        session,
        type=ConversationType.GROUP,
        title="买房决策群",
        member_ids=[me.id, *ai_ids],
    )

    loaded = await conv_repo.get_conversation(session, conversation.id)
    assert loaded is not None
    assert loaded.type is ConversationType.GROUP
    assert loaded.title == "买房决策群"
    assert {link.member.name for link in loaded.member_links} == {"我", "地产顾问", "法务"}


async def test_duplicate_member_ids_are_collapsed(session: AsyncSession) -> None:
    member_id = (await make_members(session, "唯一成员"))[0]

    conversation = await conv_repo.create_conversation(
        session,
        type=ConversationType.GROUP,
        title="测试",
        member_ids=[member_id, member_id],
    )

    assert len(conversation.member_links) == 1


async def test_create_conversation_rejects_unknown_member(session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="不存在"):
        await conv_repo.create_conversation(
            session, type=ConversationType.GROUP, member_ids=["not-exist"]
        )


async def test_add_and_remove_member(session: AsyncSession) -> None:
    me = await member_repo.get_self_member(session)
    first, second = await make_members(session, "成员甲", "成员乙")
    conversation = await conv_repo.create_conversation(
        session, type=ConversationType.GROUP, member_ids=[me.id, first]
    )

    assert await conv_repo.add_member(session, conversation.id, second) is True
    assert await conv_repo.add_member(session, conversation.id, second) is False
    assert len(conversation.member_links) == 3

    assert await conv_repo.remove_member(session, conversation.id, second) is True
    assert await conv_repo.remove_member(session, conversation.id, second) is False
    assert {link.member_id for link in conversation.member_links} == {me.id, first}


async def test_remove_member_keeps_history(session: AsyncSession) -> None:
    me = await member_repo.get_self_member(session)
    ai_id = (await make_members(session, "临时成员"))[0]
    conversation = await conv_repo.create_conversation(
        session, type=ConversationType.GROUP, member_ids=[me.id, ai_id]
    )
    await msg_repo.add_message(
        session, conversation_id=conversation.id, sender_id=ai_id, content="我先说两句"
    )

    await conv_repo.remove_member(session, conversation.id, ai_id)

    history = await msg_repo.list_messages(session, conversation.id)
    assert len(history) == 1
    assert history[0].sender.name == "临时成员"


async def test_direct_conversation_lookup(session: AsyncSession) -> None:
    me = await member_repo.get_self_member(session)
    ai_id, other_ai_id = await make_members(session, "私聊对象", "另一个对象")

    assert await conv_repo.get_direct_conversation(session, me.id, ai_id) is None

    direct = await conv_repo.create_conversation(
        session, type=ConversationType.DIRECT, member_ids=[me.id, ai_id]
    )
    found = await conv_repo.get_direct_conversation(session, me.id, ai_id)

    assert found is not None
    assert found.id == direct.id
    assert len(found.member_links) == 2

    # 已有私聊不能被复用到别的 AI 身上，否则点谁都进同一个会话
    assert await conv_repo.get_direct_conversation(session, me.id, other_ai_id) is None


async def test_conversation_list_ordered_by_activity(session: AsyncSession) -> None:
    me = await member_repo.get_self_member(session)
    ai_id = (await make_members(session, "活跃成员"))[0]
    older = await conv_repo.create_conversation(
        session, type=ConversationType.GROUP, title="旧群", member_ids=[me.id, ai_id]
    )
    newer = await conv_repo.create_conversation(
        session, type=ConversationType.GROUP, title="新群", member_ids=[me.id, ai_id]
    )

    # 显式指定活跃时间：Windows 时钟精度较粗，同一批插入的时间戳可能完全相同
    await conv_repo.touch_conversation(session, older.id, at=datetime(2026, 1, 1, tzinfo=UTC))
    await conv_repo.touch_conversation(session, newer.id, at=datetime(2026, 1, 2, tzinfo=UTC))

    assert [item.id for item in await conv_repo.list_conversations(session)] == [
        newer.id,
        older.id,
    ]

    await conv_repo.touch_conversation(session, older.id, at=datetime(2026, 1, 3, tzinfo=UTC))

    assert [item.id for item in await conv_repo.list_conversations(session)] == [
        older.id,
        newer.id,
    ]
