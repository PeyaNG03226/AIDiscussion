"""上下文压缩：把「更早的历史」折叠成摘要。

策略是滑动窗口 + 增量摘要：
- 最近的 keep_recent 条消息始终原样保留，保证对话连贯；
- 超出 token 预算的更早消息交给模型压缩，并与既有摘要合并；
- 摘要记录 up_to_message_id，下次只处理它之后的新消息，不会重复压缩。
压缩失败时不阻塞聊天，退回硬截断（直接丢弃最旧消息）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.config import get_settings
from aidiscussion.db.models import Conversation, Message
from aidiscussion.llm.base import LLMClient, LLMMessage
from aidiscussion.llm.errors import LLMError
from aidiscussion.repositories import messages as message_repo
from aidiscussion.repositories import summaries as summary_repo
from aidiscussion.services.tokens import estimate_history_tokens

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM_PROMPT = """你负责压缩群聊历史。要求：
1. 保留事实、结论、待办、用户的偏好和尚未解决的问题；
2. 省略寒暄、重复内容和无信息量的对话；
3. 用第三人称陈述，控制在 300 字以内；
4. 直接输出压缩后的摘要，不要加标题或解释。"""


@dataclass(frozen=True, slots=True)
class ContextWindow:
    """一次回复可用的历史视图。"""

    summary: str | None
    up_to_message_id: int
    max_tokens: int


def _format_history(history: Sequence[Message]) -> str:
    return "\n".join(f"{message.sender.name}：{message.content}" for message in history)


async def _summarize(
    llm: LLMClient,
    previous: str | None,
    history: Sequence[Message],
) -> str | None:
    sections = []
    if previous:
        sections.append(f"现有摘要：\n{previous}")
    sections.append("需要并入摘要的新对话：\n" + _format_history(history))

    messages = [
        LLMMessage(role="system", content=SUMMARY_SYSTEM_PROMPT),
        LLMMessage(role="user", content="\n\n".join(sections)),
    ]
    try:
        text = "".join(
            [chunk async for chunk in llm.stream_chat(messages, temperature=0.2)]
        ).strip()
    except LLMError as exc:
        logger.warning("历史压缩失败，退回硬截断：%s", exc)
        return None

    return text or None


async def prepare_window(
    session: AsyncSession,
    llm: LLMClient,
    conversation: Conversation,
    *,
    max_tokens: int | None = None,
    keep_recent: int | None = None,
    history_limit: int = 20,
) -> ContextWindow:
    """按预算压缩历史，返回摘要与「摘要已覆盖到哪条消息」。"""
    settings = get_settings()
    budget = max_tokens if max_tokens is not None else settings.context_max_tokens
    keep = keep_recent if keep_recent is not None else settings.context_keep_recent
    # 逐字保留的条数必须小于本轮读取上限，否则一条新消息都留不下
    keep = max(1, min(keep, max(1, history_limit - 1)))

    row = await summary_repo.get_summary(session, conversation.id)
    summary = row.content.strip() if row and row.content.strip() else None
    up_to = row.up_to_message_id if row else 0

    pending = await message_repo.messages_after(
        session, conversation.id, after_id=up_to
    )
    if len(pending) <= keep or estimate_history_tokens(pending) <= budget:
        return ContextWindow(summary=summary, up_to_message_id=up_to, max_tokens=budget)

    # 离线 Mock 只会复读输入，产出的「摘要」没有意义，直接走硬截断
    if not getattr(llm, "supports_summary", True):
        return ContextWindow(summary=summary, up_to_message_id=up_to, max_tokens=budget)

    older = pending[:-keep]
    compressed = await _summarize(llm, summary, older)
    if compressed is None:
        # 压缩失败时保留原摘要，让调用方用硬截断兜底
        return ContextWindow(summary=summary, up_to_message_id=up_to, max_tokens=budget)

    up_to = older[-1].id
    await summary_repo.save_summary(
        session,
        conversation_id=conversation.id,
        content=compressed,
        up_to_message_id=up_to,
    )
    logger.info(
        "会话 %s 压缩了 %d 条历史，摘要覆盖到消息 %s",
        conversation.id,
        len(older),
        up_to,
    )
    return ContextWindow(summary=compressed, up_to_message_id=up_to, max_tokens=budget)
