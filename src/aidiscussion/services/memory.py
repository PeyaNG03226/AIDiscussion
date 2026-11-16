"""长期记忆：从用户消息里提取事实，并在后续对话中召回。

- 提取：每条足够长的用户消息触发一次轻量调用，产出 0~3 条记忆；
- 存储：记忆跨会话共享，群聊里记下的信息私聊也能用；
- 召回：按中文二元组 / 英文词的重合度打分，取最相关的若干条注入 system prompt。

这套方案不引入向量库：数据规模是个人工作台级别，关键词重合已经够用，
而且完全可解释、可测试、零额外依赖。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.config import get_settings
from aidiscussion.db.models import Memory
from aidiscussion.llm.base import LLMClient, LLMMessage
from aidiscussion.llm.errors import LLMError
from aidiscussion.repositories import memories as memory_repo

logger = logging.getLogger(__name__)

EXTRACT_SYSTEM_PROMPT = """你是一个记忆提取器。从用户消息中提取值得长期记住的信息：
- 用户的身份、职业、所在地
- 偏好、习惯、忌讳
- 正在做的项目、目标、计划
- 明确要求你记住的事

只输出 JSON 数组，每个元素是一句不超过 40 字的陈述；没有值得记住的内容就输出 []。
不要臆测，不要收录一次性的闲聊。示例：["用户正在开发一个 AI 群聊项目"]"""

MIN_CONTENT_LENGTH = 12
MAX_MEMORIES_PER_MESSAGE = 3

_FEATURE_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9_+#.-]*|[\u4e00-\u9fff]")
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def should_extract(content: str) -> bool:
    """太短的消息基本都是寒暄，不值得为此多花一次调用。"""
    return len(content.strip()) >= MIN_CONTENT_LENGTH


def _features(text: str) -> set[str]:
    lowered = text.lower()
    tokens = _FEATURE_PATTERN.findall(lowered)
    words = {token for token in tokens if not _CJK_PATTERN.fullmatch(token) and len(token) > 1}
    cjk = [token for token in tokens if _CJK_PATTERN.fullmatch(token)]
    bigrams = {left + right for left, right in zip(cjk, cjk[1:], strict=False)}
    return words | bigrams


def parse_memories(raw: str) -> list[str]:
    """宽容地解析模型输出：允许 JSON 前后带解释或 Markdown 代码块。"""
    match = re.search(r"\[[\s\S]*\]", raw)
    if match is None:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    results: list[str] = []
    for item in data:
        if not isinstance(item, str):
            continue
        content = item.strip().strip("-•").strip()
        if 2 <= len(content) <= 200 and content not in results:
            results.append(content)
    return results[:MAX_MEMORIES_PER_MESSAGE]


async def extract_and_store(
    session: AsyncSession,
    llm: LLMClient,
    *,
    content: str,
    conversation_id: str | None = None,
) -> list[Memory]:
    """从一条用户消息里提取记忆并落库。任何失败都只记日志，不影响聊天。"""
    if not get_settings().memory_enabled or not should_extract(content):
        return []

    messages = [
        LLMMessage(role="system", content=EXTRACT_SYSTEM_PROMPT),
        LLMMessage(role="user", content=content),
    ]
    try:
        raw = "".join(
            [chunk async for chunk in llm.stream_chat(messages, temperature=0.1)]
        )
    except LLMError as exc:
        logger.warning("记忆提取失败：%s", exc)
        return []

    added: list[Memory] = []
    for candidate in parse_memories(raw):
        if await memory_repo.find_by_content(session, candidate) is not None:
            continue
        added.append(
            await memory_repo.add_memory(
                session,
                content=candidate,
                source_conversation_id=conversation_id,
            )
        )
    if added:
        logger.info("从用户消息中新增 %d 条长期记忆", len(added))
    return added


async def recall(
    session: AsyncSession,
    query: str,
    *,
    limit: int | None = None,
) -> list[str]:
    """按关键词重合度召回记忆，并刷新 last_used_at。"""
    settings = get_settings()
    if not settings.memory_enabled:
        return []

    query_features = _features(query)
    if not query_features:
        return []

    scored: list[tuple[int, Memory]] = []
    for memory in await memory_repo.search_candidates(session):
        score = len(query_features & _features(memory.content))
        # 至少两个特征重合，避免「我」「的」这类单字造成误召回
        if score >= 2:
            scored.append((score, memory))

    scored.sort(key=lambda pair: (-pair[0], -pair[1].id))
    top_k = limit if limit is not None else settings.memory_top_k
    chosen = [memory for _, memory in scored[: max(0, top_k)]]

    if chosen:
        now = datetime.now(UTC)
        for memory in chosen:
            memory.last_used_at = now
        await session.flush()

    return [memory.content for memory in chosen]


async def list_all(session: AsyncSession) -> Sequence[Memory]:
    return await memory_repo.list_memories(session)
