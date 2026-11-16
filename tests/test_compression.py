"""上下文压缩测试：何时触发、摘要如何增量合并、失败如何兜底。"""

from collections.abc import AsyncIterator, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.db.models import ConversationType, Member
from aidiscussion.llm.base import LLMMessage
from aidiscussion.llm.errors import LLMConnectionError
from aidiscussion.llm.mock import MockLLM
from aidiscussion.repositories import conversations as conv_repo
from aidiscussion.repositories import members as member_repo
from aidiscussion.repositories import messages as message_repo
from aidiscussion.repositories import summaries as summary_repo
from aidiscussion.services import compression


class SummaryLLM:
    def __init__(self, reply: str = "摘要：聊了房价和预算", *, fail: bool = False) -> None:
        self.reply = reply
        self.fail = fail
        self.calls: list[list[LLMMessage]] = []

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        self.calls.append(list(messages))
        if self.fail:
            raise LLMConnectionError("模型连接中断")
        for char in self.reply:
            yield char

    async def aclose(self) -> None:
        return None


async def setup(session: AsyncSession) -> tuple[Member, Member, object]:
    me = await member_repo.get_self_member(session)
    ai = await member_repo.create_member(session, name="顾问", system_prompt="你是顾问")
    conversation = await conv_repo.create_conversation(
        session,
        type=ConversationType.GROUP,
        title="群聊",
        member_ids=[me.id, ai.id],
    )
    return me, ai, conversation


async def add_history(
    session: AsyncSession,
    conversation_id: str,
    me: Member,
    ai: Member,
    *,
    count: int,
    size: int = 30,
) -> None:
    for index in range(count):
        sender = me if index % 2 == 0 else ai
        await message_repo.add_message(
            session,
            conversation_id=conversation_id,
            sender_id=sender.id,
            content=f"第{index}条：" + "内容" * size,
        )


async def test_short_history_is_left_untouched(session: AsyncSession) -> None:
    me, ai, conversation = await setup(session)
    await add_history(session, conversation.id, me, ai, count=4, size=2)
    llm = SummaryLLM()

    window = await compression.prepare_window(
        session, llm, conversation, max_tokens=5000, keep_recent=2
    )

    assert llm.calls == []
    assert window.summary is None
    assert window.up_to_message_id == 0


async def test_long_history_is_compressed_and_persisted(session: AsyncSession) -> None:
    me, ai, conversation = await setup(session)
    await add_history(session, conversation.id, me, ai, count=8)
    llm = SummaryLLM()

    window = await compression.prepare_window(
        session, llm, conversation, max_tokens=10, keep_recent=2
    )

    assert window.summary == "摘要：聊了房价和预算"
    assert window.up_to_message_id == 6  # 8 条里保留最近 2 条，压掉前 6 条
    assert len(llm.calls) == 1
    prompt = "\n".join(item.content for item in llm.calls[0])
    assert "第0条" in prompt and "第7条" not in prompt

    row = await summary_repo.get_summary(session, conversation.id)
    assert row is not None
    assert row.content == window.summary
    assert row.up_to_message_id == 6


async def test_next_round_merges_previous_summary(session: AsyncSession) -> None:
    me, ai, conversation = await setup(session)
    await add_history(session, conversation.id, me, ai, count=8)
    llm = SummaryLLM("第一版摘要")

    await compression.prepare_window(
        session, llm, conversation, max_tokens=10, keep_recent=2
    )
    await add_history(session, conversation.id, me, ai, count=4)
    llm.reply = "第二版摘要"
    window = await compression.prepare_window(
        session, llm, conversation, max_tokens=10, keep_recent=2
    )

    merged_prompt = "\n".join(item.content for item in llm.calls[1])
    assert "现有摘要：\n第一版摘要" in merged_prompt
    assert window.summary == "第二版摘要"
    assert window.up_to_message_id == 10  # 12 条里保留 2 条
    row = await summary_repo.get_summary(session, conversation.id)
    assert row is not None and row.content == "第二版摘要"


async def test_compression_failure_falls_back_without_persisting(session: AsyncSession) -> None:
    me, ai, conversation = await setup(session)
    await add_history(session, conversation.id, me, ai, count=8)
    llm = SummaryLLM(fail=True)

    window = await compression.prepare_window(
        session, llm, conversation, max_tokens=10, keep_recent=2
    )

    assert window.summary is None
    assert window.up_to_message_id == 0
    assert await summary_repo.get_summary(session, conversation.id) is None


async def test_mock_llm_skips_summary_generation(session: AsyncSession) -> None:
    me, ai, conversation = await setup(session)
    await add_history(session, conversation.id, me, ai, count=8)

    window = await compression.prepare_window(
        session, MockLLM(), conversation, max_tokens=10, keep_recent=2
    )

    assert window.summary is None
    assert await summary_repo.get_summary(session, conversation.id) is None


async def test_keep_recent_never_exceeds_history_limit(session: AsyncSession) -> None:
    me, ai, conversation = await setup(session)
    await add_history(session, conversation.id, me, ai, count=8)
    llm = SummaryLLM()

    window = await compression.prepare_window(
        session, llm, conversation, max_tokens=10, keep_recent=12, history_limit=3
    )

    # keep_recent 被压到 history_limit - 1 = 2，否则一条新消息都保留不下来
    assert window.up_to_message_id == 6
