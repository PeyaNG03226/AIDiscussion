"""Token 预算估算。

这里不需要精确的分词器：目标只是给上下文划一条「大概不能超过」的线。
经验上中文约 1 字 1 token，英文约 4 字符 1 token，按这个比例估算足够稳。
"""

import math
import re
from collections.abc import Sequence

from aidiscussion.db.models import Message

_CJK_PATTERN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")

# 每条消息除了正文，还有角色、分隔符等固定开销
MESSAGE_OVERHEAD = 4


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(_CJK_PATTERN.findall(text))
    rest = len(text) - cjk
    return cjk + math.ceil(rest / 4)


def estimate_history_tokens(history: Sequence[Message]) -> int:
    return sum(estimate_tokens(message.content) + MESSAGE_OVERHEAD for message in history)


def trim_history(history: Sequence[Message], max_tokens: int) -> list[Message]:
    """从最旧的消息开始丢弃，直到回到预算内；至少保留最新一条。"""
    trimmed = list(history)
    while len(trimmed) > 1 and estimate_history_tokens(trimmed) > max_tokens:
        trimmed.pop(0)
    return trimmed
