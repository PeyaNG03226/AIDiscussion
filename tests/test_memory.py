"""长期记忆测试：提取、去重、召回与开关。"""

from collections.abc import AsyncIterator, Sequence

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.config import get_settings
from aidiscussion.db.models import ConversationType
from aidiscussion.llm.base import LLMMessage
from aidiscussion.llm.errors import LLMConnectionError
from aidiscussion.repositories import conversations as conv_repo
from aidiscussion.repositories import members as member_repo
from aidiscussion.repositories import memories as memory_repo
from aidiscussion.services import memory


class MemoryLLM:
    def __init__(self, reply: str, *, fail: bool = False) -> None:
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


async def setup_conversation(session: AsyncSession) -> str:
    me = await member_repo.get_self_member(session)
    ai = await member_repo.create_member(session, name="顾问")
    conversation = await conv_repo.create_conversation(
        session,
        type=ConversationType.DIRECT,
        title="私聊",
        member_ids=[me.id, ai.id],
    )
    return conversation.id


def test_parse_memories_accepts_json_with_noise() -> None:
    raw = '好的：\n```json\n["用户喜欢简洁的回答", " 用户住在杭州 ", 42]\n```'

    assert memory.parse_memories(raw) == ["用户喜欢简洁的回答", "用户住在杭州"]


def test_parse_memories_returns_empty_on_invalid_output() -> None:
    assert memory.parse_memories("没有值得记住的内容") == []
    assert memory.parse_memories("[不是合法 JSON]") == []


def test_should_extract_skips_short_messages() -> None:
    assert memory.should_extract("你好") is False
    assert memory.should_extract("我在做一个 AI 群聊项目") is True


async def test_extract_and_store_dedupes(session: AsyncSession) -> None:
    conversation_id = await setup_conversation(session)
    llm = MemoryLLM(
        '["用户正在开发一个 AI 群聊项目", "用户喜欢简洁的回答", "用户正在开发一个 AI 群聊项目"]'
    )

    added = await memory.extract_and_store(
        session,
        llm,
        content="我在做一个 AI 群聊项目，比较喜欢简洁的回答",
        conversation_id=conversation_id,
    )

    assert [item.content for item in added] == [
        "用户正在开发一个 AI 群聊项目",
        "用户喜欢简洁的回答",
    ]
    assert all(item.source_conversation_id == conversation_id for item in added)

    again = await memory.extract_and_store(
        session,
        llm,
        content="再说一次，我在做 AI 群聊项目",
        conversation_id=conversation_id,
    )
    assert again == []


async def test_short_message_skips_llm_call(session: AsyncSession) -> None:
    await setup_conversation(session)
    llm = MemoryLLM("[]")

    added = await memory.extract_and_store(session, llm, content="你好")

    assert added == []
    assert llm.calls == []


async def test_extraction_failure_is_silent(session: AsyncSession) -> None:
    await setup_conversation(session)
    llm = MemoryLLM("[]", fail=True)

    added = await memory.extract_and_store(
        session, llm, content="我在做一个 AI 群聊项目，想记住这一点"
    )

    assert added == []


async def test_recall_scores_by_keyword_overlap(session: AsyncSession) -> None:
    project = await memory_repo.add_memory(
        session, content="用户正在开发一个 AI 群聊项目"
    )
    await memory_repo.add_memory(session, content="用户喜欢喝美式咖啡")
    await memory_repo.add_memory(session, content="用户住在杭州")

    recalled = await memory.recall(session, "我想继续推进 AI 群聊项目")

    assert recalled == [project.content]
    assert project.last_used_at is not None


async def test_recall_respects_limit(session: AsyncSession) -> None:
    await memory_repo.add_memory(session, content="用户喜欢喝美式咖啡")
    await memory_repo.add_memory(session, content="用户喜欢喝拿铁咖啡")

    recalled = await memory.recall(session, "美式咖啡好喝吗", limit=1)

    assert len(recalled) == 1


async def test_recall_can_be_disabled(session: AsyncSession, monkeypatch) -> None:
    await memory_repo.add_memory(session, content="用户喜欢喝美式咖啡")
    settings = get_settings()
    monkeypatch.setattr(settings, "memory_enabled", False)

    assert await memory.recall(session, "咖啡喝什么好") == []


async def test_unrelated_query_recalls_nothing(session: AsyncSession) -> None:
    await memory_repo.add_memory(session, content="用户正在开发一个 AI 群聊项目")

    assert await memory.recall(session, "今天天气不错") == []


@pytest.mark.parametrize("query", ["", "a"])
async def test_empty_query_features_recall_nothing(
    session: AsyncSession, query: str
) -> None:
    await memory_repo.add_memory(session, content="用户正在开发一个 AI 群聊项目")

    assert await memory.recall(session, query) == []
