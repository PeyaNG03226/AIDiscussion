"""消息仓储测试。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.db.models import ConversationType
from aidiscussion.repositories import conversations as conv_repo
from aidiscussion.repositories import members as member_repo
from aidiscussion.repositories import messages as msg_repo


async def setup_conversation(session: AsyncSession) -> tuple[str, str, str]:
    me = await member_repo.get_self_member(session)
    ai = await member_repo.create_member(session, name="记录助手")
    conversation = await conv_repo.create_conversation(
        session,
        type=ConversationType.GROUP,
        title="测试群",
        member_ids=[me.id, ai.id],
    )
    return conversation.id, me.id, ai.id


async def test_add_message_updates_activity(session: AsyncSession) -> None:
    conversation_id, me_id, _ = await setup_conversation(session)
    conversation = await conv_repo.get_conversation(session, conversation_id)
    assert conversation is not None
    assert conversation.last_message_at is None

    message = await msg_repo.add_message(
        session, conversation_id=conversation_id, sender_id=me_id, content="你好"
    )

    assert message.id is not None
    assert conversation.last_message_at == message.created_at


async def test_list_messages_is_chronological(session: AsyncSession) -> None:
    conversation_id, me_id, ai_id = await setup_conversation(session)
    await msg_repo.add_message(
        session, conversation_id=conversation_id, sender_id=me_id, content="第一条"
    )
    await msg_repo.add_message(
        session, conversation_id=conversation_id, sender_id=ai_id, content="第二条"
    )

    history = await msg_repo.list_messages(session, conversation_id)

    assert [message.content for message in history] == ["第一条", "第二条"]
    assert history[1].sender.name == "记录助手"


async def test_pagination_uses_before_id(session: AsyncSession) -> None:
    conversation_id, me_id, _ = await setup_conversation(session)
    for index in range(5):
        await msg_repo.add_message(
            session, conversation_id=conversation_id, sender_id=me_id, content=f"消息{index}"
        )

    latest = await msg_repo.list_messages(session, conversation_id, limit=2)
    earlier = await msg_repo.list_messages(
        session, conversation_id, limit=2, before_id=latest[0].id
    )

    assert [message.content for message in latest] == ["消息3", "消息4"]
    assert [message.content for message in earlier] == ["消息1", "消息2"]


async def test_empty_content_rejected(session: AsyncSession) -> None:
    conversation_id, me_id, _ = await setup_conversation(session)

    with pytest.raises(ValueError, match="不能为空"):
        await msg_repo.add_message(
            session, conversation_id=conversation_id, sender_id=me_id, content="   "
        )


async def test_recent_messages_supports_exclusion(session: AsyncSession) -> None:
    conversation_id, me_id, ai_id = await setup_conversation(session)
    for index in range(3):
        await msg_repo.add_message(
            session, conversation_id=conversation_id, sender_id=me_id, content=f"问题{index}"
        )
    answer = await msg_repo.add_message(
        session, conversation_id=conversation_id, sender_id=ai_id, content="回答"
    )

    recent = await msg_repo.recent_messages(
        session, conversation_id, limit=10, exclude_ids=[answer.id]
    )

    assert [message.content for message in recent] == ["问题0", "问题1", "问题2"]
    assert await msg_repo.count_messages(session, conversation_id) == 4
