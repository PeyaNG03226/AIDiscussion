"""会话编排：决定「这条消息由谁回答」，并逐字产出回复。

三种群聊模式：
- mention：只有被 @ 的成员回复，最省 token
- free：所有 AI 成员依次各回复一轮
- supervisor：多一次调用，由「主持人」挑选最合适的成员

轮次上限 max_replies 是硬约束：没有它，自由讨论很容易变成烧 token 的无底洞。
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.db.models import (
    Conversation,
    ConversationType,
    Member,
    MemberKind,
    Message,
    MessageStatus,
)
from aidiscussion.llm.base import LLMClient, LLMMessage
from aidiscussion.llm.errors import LLMError
from aidiscussion.repositories import messages as message_repo
from aidiscussion.services import compression, memory
from aidiscussion.services.context import build_prompt
from aidiscussion.services.tokens import trim_history

logger = logging.getLogger(__name__)

_MENTION_PATTERN = re.compile(r"@([^\s@，。,.!?！？:：]{1,20})")
_ALL_ALIASES = {"all", "全体", "所有人"}


class ReplyMode:
    """用字符串常量而不是枚举，便于配置与接口传参。"""

    MENTION = "mention"
    FREE = "free"
    SUPERVISOR = "supervisor"


@dataclass(frozen=True, slots=True)
class ReplyStart:
    member: Member


@dataclass(frozen=True, slots=True)
class ReplyDelta:
    member_id: str
    text: str


@dataclass(frozen=True, slots=True)
class ReplyDone:
    member: Member
    message: Message


@dataclass(frozen=True, slots=True)
class ReplyFailed:
    member_id: str
    error: str


ChatEvent = ReplyStart | ReplyDelta | ReplyDone | ReplyFailed


def parse_mentions(content: str) -> list[str]:
    return [match.group(1) for match in _MENTION_PATTERN.finditer(content)]


def ai_members_of(conversation: Conversation) -> list[Member]:
    return [
        link.member
        for link in conversation.member_links
        if link.member.kind is MemberKind.AI and link.member.is_active
    ]


def select_targets(
    conversation: Conversation,
    content: str,
    mode: str,
    *,
    max_replies: int = 4,
) -> list[Member]:
    """纯函数：根据会话类型与模式决定本轮谁发言。"""
    ai_members = ai_members_of(conversation)

    if conversation.type is ConversationType.DIRECT:
        return ai_members[:1]

    if mode == ReplyMode.FREE:
        targets = ai_members
    elif mode == ReplyMode.MENTION:
        names = parse_mentions(content)
        if any(name.lower() in _ALL_ALIASES for name in names):
            targets = ai_members
        else:
            targets = [member for member in ai_members if member.name in names]
    else:
        raise ValueError(f"未知的群聊模式：{mode}")

    return targets[: max(0, max_replies)]


async def choose_speaker(
    llm: LLMClient,
    conversation: Conversation,
    user_message: Message,
) -> Member | None:
    """主持人模式：让模型在成员里挑一个，失败则退回第一个可用成员。"""
    ai_members = ai_members_of(conversation)
    if not ai_members:
        return None

    roster = "\n".join(
        f"- {member.name}：{member.system_prompt.strip()[:40] or '普通成员'}"
        for member in ai_members
    )
    messages = [
        LLMMessage(
            role="system",
            content="你是群聊主持人，负责挑选最适合回答当前消息的成员。只输出成员名字，不要解释。",
        ),
        LLMMessage(
            role="user",
            content=f"可选成员：\n{roster}\n\n用户消息：{user_message.content}\n\n请只输出一个成员名字。",
        ),
    ]

    try:
        answer = "".join(
            [chunk async for chunk in llm.stream_chat(messages, temperature=0.0)]
        ).strip()
    except LLMError as exc:
        logger.warning("主持人选择失败，退回第一位成员：%s", exc)
        return ai_members[0]

    return next((member for member in ai_members if member.name in answer), ai_members[0])


async def post_user_message(
    session: AsyncSession,
    *,
    conversation_id: str,
    sender_id: str,
    content: str,
) -> Message:
    return await message_repo.add_message(
        session,
        conversation_id=conversation_id,
        sender_id=sender_id,
        content=content,
    )


async def generate_replies(
    session: AsyncSession,
    llm: LLMClient,
    *,
    conversation: Conversation,
    user_message: Message,
    mode: str = ReplyMode.MENTION,
    max_replies: int = 4,
    history_limit: int = 20,
) -> AsyncIterator[ChatEvent]:
    is_group = conversation.type is ConversationType.GROUP

    if mode == ReplyMode.SUPERVISOR:
        speaker = await choose_speaker(llm, conversation, user_message)
        targets: Sequence[Member] = [speaker] if speaker is not None else []
    else:
        targets = select_targets(
            conversation, user_message.content, mode, max_replies=max_replies
        )

    roster = [link.member for link in conversation.member_links]

    # 只在一轮真的要发言时才做压缩与记忆召回，纯静默的消息不产生额外开销
    window = await compression.prepare_window(
        session, llm, conversation, history_limit=history_limit
    )
    recalled = await memory.recall(session, user_message.content)

    for member in targets:
        # 每个成员都重新读一次历史：同一轮里先发言的 AI，后面的 AI 能看到
        history = await message_repo.recent_messages(
            session, conversation.id, limit=history_limit
        )
        # 已经被摘要覆盖的消息不再重复发送
        history = [item for item in history if item.id > window.up_to_message_id]
        history = trim_history(history, window.max_tokens)
        prompt = build_prompt(
            member=member,
            history=history,
            roster=roster,
            is_group=is_group,
            summary=window.summary,
            memories=recalled,
        )

        yield ReplyStart(member=member)

        buffer: list[str] = []
        try:
            async for chunk in llm.stream_chat(
                prompt,
                model=member.model or None,
                temperature=member.temperature,
            ):
                buffer.append(chunk)
                yield ReplyDelta(member_id=member.id, text=chunk)
        except LLMError as exc:
            partial = "".join(buffer).strip()
            if partial:
                await message_repo.add_message(
                    session,
                    conversation_id=conversation.id,
                    sender_id=member.id,
                    content=partial,
                    status=MessageStatus.FAILED,
                )
            logger.warning("成员 %s 回复失败：%s", member.name, exc)
            yield ReplyFailed(member_id=member.id, error=str(exc))
            continue

        content = "".join(buffer).strip()
        if not content:
            yield ReplyFailed(member_id=member.id, error="模型没有返回内容")
            continue

        saved = await message_repo.add_message(
            session,
            conversation_id=conversation.id,
            sender_id=member.id,
            content=content,
        )
        yield ReplyDone(member=member, message=saved)
