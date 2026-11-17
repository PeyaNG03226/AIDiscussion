"""会话编排测试：谁回复、回复内容是否落库、失败怎么处理。"""

import random
from collections.abc import AsyncIterator, Sequence

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.db.models import ConversationType, Member, MessageStatus
from aidiscussion.llm.base import LLMMessage
from aidiscussion.llm.errors import LLMConnectionError
from aidiscussion.repositories import conversations as conv_repo
from aidiscussion.repositories import members as member_repo
from aidiscussion.repositories import memories as memory_repo
from aidiscussion.repositories import messages as message_repo
from aidiscussion.services import chat
from aidiscussion.services.chat import ReplyDelta, ReplyDone, ReplyFailed, ReplyStart


class FakeLLM:
    """按顺序返回预设回复，同时记录每次调用收到的消息。"""

    def __init__(self, *responses: str, chunk_size: int = 4) -> None:
        self._queue = list(responses)
        self._chunk_size = max(1, chunk_size)
        self.calls: list[list[LLMMessage]] = []

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        self.calls.append(list(messages))
        text = self._queue.pop(0) if self._queue else "好的"
        for start in range(0, len(text), self._chunk_size):
            yield text[start : start + self._chunk_size]

    async def aclose(self) -> None:
        return None


class FailingLLM:
    def __init__(self, partial: str = "") -> None:
        self._partial = partial

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        if self._partial:
            yield self._partial
        raise LLMConnectionError("模型连接中断")

    async def aclose(self) -> None:
        return None


async def setup_conversation(
    session: AsyncSession,
    *,
    names: Sequence[str] = ("法务", "地产顾问"),
    direct: bool = False,
) -> tuple[Member, list[Member], object]:
    me = await member_repo.get_self_member(session)
    ai_members = [
        await member_repo.create_member(session, name=name, system_prompt=f"你是{name}")
        for name in names
    ]
    conversation = await conv_repo.create_conversation(
        session,
        type=ConversationType.DIRECT if direct else ConversationType.GROUP,
        title="私聊" if direct else "群聊",
        member_ids=[me.id, *[member.id for member in ai_members]],
    )
    return me, ai_members, conversation


async def run_replies(session: AsyncSession, llm, conversation, message, **kwargs) -> list:
    return [
        event
        async for event in chat.generate_replies(
            session, llm, conversation=conversation, user_message=message, **kwargs
        )
    ]


async def test_each_member_uses_its_own_provider_client(session: AsyncSession) -> None:
    me = await member_repo.get_self_member(session)
    doubao = await member_repo.create_member(session, name="豆包", provider="doubao")
    kimi = await member_repo.create_member(session, name="Kimi", provider="kimi")
    conversation = await conv_repo.create_conversation(
        session,
        type=ConversationType.GROUP,
        title="多厂商群",
        member_ids=[me.id, doubao.id, kimi.id],
    )
    message = await chat.post_user_message(
        session, conversation_id=conversation.id, sender_id=me.id, content="大家好"
    )

    clients = {"doubao": FakeLLM("豆包在此"), "kimi": FakeLLM("Kimi 在此")}
    used: list[str] = []

    def resolve(member: Member):
        used.append(member.provider)
        return clients[member.provider]

    events = await run_replies(
        session,
        FakeLLM("默认模型不该被用到"),
        conversation,
        message,
        mode=chat.ReplyMode.FREE,
        llm_for_member=resolve,
    )

    assert sorted(used) == ["doubao", "kimi"]
    assert {event.member.name for event in events if isinstance(event, ReplyStart)} == {
        "豆包",
        "Kimi",
    }
    assert clients["doubao"].calls and clients["kimi"].calls


async def test_direct_chat_replies_and_persists(session: AsyncSession) -> None:
    me, (assistant,), conversation = await setup_conversation(
        session, names=("私人助理",), direct=True
    )
    message = await chat.post_user_message(
        session, conversation_id=conversation.id, sender_id=me.id, content="在吗"
    )

    events = await run_replies(session, FakeLLM("在的，有什么可以帮你"), conversation, message)

    assert isinstance(events[0], ReplyStart)
    assert isinstance(events[-1], ReplyDone)
    assert events[-1].member.id == assistant.id
    assert "".join(e.text for e in events if isinstance(e, ReplyDelta)) == "在的，有什么可以帮你"

    history = await message_repo.list_messages(session, conversation.id)
    assert [item.content for item in history] == ["在吗", "在的，有什么可以帮你"]
    assert history[1].sender_id == assistant.id


async def test_mention_mode_only_answers_named_member(session: AsyncSession) -> None:
    me, ai_members, conversation = await setup_conversation(session)
    message = await chat.post_user_message(
        session,
        conversation_id=conversation.id,
        sender_id=me.id,
        content="@法务 这个合同能签吗",
    )

    events = await run_replies(session, FakeLLM("先看违约金条款"), conversation, message)

    done = [event for event in events if isinstance(event, ReplyDone)]
    assert [item.member.name for item in done] == ["法务"]
    history = await message_repo.list_messages(session, conversation.id)
    assert {item.sender_id for item in history} == {me.id, ai_members[0].id}


async def test_mention_without_name_stays_silent(session: AsyncSession) -> None:
    me, _, conversation = await setup_conversation(session)
    message = await chat.post_user_message(
        session, conversation_id=conversation.id, sender_id=me.id, content="大家好"
    )

    llm = FakeLLM()
    events = await run_replies(session, llm, conversation, message, mode=chat.ReplyMode.MENTION)

    assert events == []
    assert llm.calls == []


async def test_free_mode_lets_members_see_each_other(session: AsyncSession) -> None:
    me, ai_members, conversation = await setup_conversation(session)
    message = await chat.post_user_message(
        session, conversation_id=conversation.id, sender_id=me.id, content="聊聊房价"
    )
    llm = FakeLLM("我先说，最近确实在跌", "补充一句，成交量也低了")

    events = await run_replies(session, llm, conversation, message, mode=chat.ReplyMode.FREE)

    done = [event for event in events if isinstance(event, ReplyDone)]
    # 自由模式顺序是随机的，这里只校验「两人都发言」且后发者能看到先发者的内容
    assert {item.member.name for item in done} == {"法务", "地产顾问"}
    assert len(llm.calls) == 2

    second_prompt = "\n".join(item.content for item in llm.calls[1])
    assert "我先说，最近确实在跌" in second_prompt


async def test_max_replies_caps_the_round(session: AsyncSession) -> None:
    me, _, conversation = await setup_conversation(
        session, names=("法务", "地产顾问", "吐槽担当")
    )
    message = await chat.post_user_message(
        session, conversation_id=conversation.id, sender_id=me.id, content="随便聊聊"
    )
    llm = FakeLLM("一", "二", "三")

    events = await run_replies(
        session, llm, conversation, message, mode=chat.ReplyMode.FREE, max_replies=2
    )

    assert len([event for event in events if isinstance(event, ReplyDone)]) == 2
    assert len(llm.calls) == 2


async def test_supervisor_chooses_speaker(session: AsyncSession) -> None:
    me, _, conversation = await setup_conversation(session, names=("法务", "地产顾问"))
    message = await chat.post_user_message(
        session, conversation_id=conversation.id, sender_id=me.id, content="这套房值不值"
    )
    llm = FakeLLM("地产顾问", "从地段看还算合理")

    events = await run_replies(
        session, llm, conversation, message, mode=chat.ReplyMode.SUPERVISOR
    )

    done = [event for event in events if isinstance(event, ReplyDone)]
    assert [item.member.name for item in done] == ["地产顾问"]
    assert len(llm.calls) == 2
    assert "可选成员" in llm.calls[0][-1].content


async def test_failed_reply_does_not_persist_message(session: AsyncSession) -> None:
    me, _, conversation = await setup_conversation(session, names=("法务",))
    message = await chat.post_user_message(
        session, conversation_id=conversation.id, sender_id=me.id, content="你好"
    )

    events = await run_replies(
        session, FailingLLM(), conversation, message, mode=chat.ReplyMode.FREE
    )

    assert isinstance(events[-1], ReplyFailed)
    history = await message_repo.list_messages(session, conversation.id)
    assert [item.content for item in history] == ["你好"]


async def test_partial_reply_is_saved_as_failed(session: AsyncSession) -> None:
    me, _, conversation = await setup_conversation(session, names=("法务",))
    message = await chat.post_user_message(
        session, conversation_id=conversation.id, sender_id=me.id, content="你好"
    )

    events = await run_replies(
        session, FailingLLM(partial="我先说一半"), conversation, message, mode=chat.ReplyMode.FREE
    )

    assert isinstance(events[-1], ReplyFailed)
    history = await message_repo.list_messages(session, conversation.id)
    assert history[-1].content == "我先说一半"
    assert history[-1].status is MessageStatus.FAILED


def test_parse_mentions() -> None:
    assert chat.parse_mentions("@法务 看看这个") == ["法务"]
    assert chat.parse_mentions("@法务 @地产顾问 你们说") == ["法务", "地产顾问"]
    assert chat.parse_mentions("没有提到谁") == []


async def test_recalled_memories_are_injected_into_prompt(session: AsyncSession) -> None:
    me, _, conversation = await setup_conversation(session, names=("法务",))
    await memory_repo.add_memory(session, content="用户正在处理一起房屋租赁纠纷")
    message = await chat.post_user_message(
        session,
        conversation_id=conversation.id,
        sender_id=me.id,
        content="这个房屋租赁纠纷怎么处理",
    )
    llm = FakeLLM("先看合同条款")

    await run_replies(session, llm, conversation, message, mode=chat.ReplyMode.FREE)

    system = llm.calls[0][0].content
    assert "长期记忆" in system
    assert "房屋租赁纠纷" in system


async def test_mention_all_targets_everyone(session: AsyncSession) -> None:
    me, _, conversation = await setup_conversation(session, names=("法务", "地产顾问"))
    message = await chat.post_user_message(
        session, conversation_id=conversation.id, sender_id=me.id, content="@all 各位怎么看"
    )
    llm = FakeLLM("看法一", "看法二")

    events = await run_replies(session, llm, conversation, message)

    done = [event for event in events if isinstance(event, ReplyDone)]
    assert {item.member.name for item in done} == {"法务", "地产顾问"}


async def test_mention_all_aliases(session: AsyncSession) -> None:
    "「@全体成员」是大家最常输的写法，必须命中所有人，不能因为多了两个字就没人回。"
    _, _, conversation = await setup_conversation(session, names=("法务", "地产顾问"))
    ids = {member.id for member in chat.ai_members_of(conversation)}

    for content in ("@全体成员 都说说", "@所有人 看下", "@大家 评价一下"):
        targets = chat.select_targets(conversation, content, mode=chat.ReplyMode.MENTION)
        assert {member.id for member in targets} == ids


async def test_mention_keeps_user_order(session: AsyncSession) -> None:
    "点名到具体成员时按用户 @ 的顺序来，别自作主张打乱。"
    _, _, conversation = await setup_conversation(session, names=("法务", "地产顾问"))

    targets = chat.select_targets(
        conversation, "@地产顾问 先说 @法务 补充", mode=chat.ReplyMode.MENTION
    )
    assert [member.name for member in targets] == ["地产顾问", "法务"]


async def test_free_mode_order_is_shuffled(session: AsyncSession) -> None:
    "自由讨论每轮顺序随机，避免每次都按建群顺序开口。"
    _, _, conversation = await setup_conversation(session, names=("甲", "乙", "丙", "丁"))
    ids = {member.id for member in chat.ai_members_of(conversation)}

    orders = {
        tuple(
            member.id
            for member in chat.select_targets(
                conversation, "随便聊", mode=chat.ReplyMode.FREE, rng=random.Random(seed)
            )
        )
        for seed in range(8)
    }

    assert all(set(order) == ids for order in orders)
    assert len(orders) > 1


async def test_direct_chat_sees_recent_group_messages(session: AsyncSession) -> None:
    "群聊里刚出过谜题，私聊问同一个模型时必须能看到这段背景。"
    me, members, group = await setup_conversation(session, names=("豆包",))
    await chat.post_user_message(
        session, conversation_id=group.id, sender_id=me.id, content="小草连在一起 打一字"
    )
    direct = await conv_repo.create_conversation(
        session,
        type=ConversationType.DIRECT,
        title="与 豆包 的私聊",
        member_ids=[me.id, members[0].id],
    )
    message = await chat.post_user_message(
        session, conversation_id=direct.id, sender_id=me.id, content="刚刚我出的题还记得吗"
    )

    llm = FakeLLM("你出的是「小草连在一起」")
    await run_replies(session, llm, direct, message)

    system = llm.calls[0][0].content
    assert "[群聊] 我：小草连在一起 打一字" in system


async def test_invalid_mode_raises(session: AsyncSession) -> None:
    _, _, conversation = await setup_conversation(session)

    with pytest.raises(ValueError, match="未知的群聊模式"):
        chat.select_targets(conversation, "你好", mode="bogus")
