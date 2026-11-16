"""Token 估算与硬截断测试。"""

from aidiscussion.db.models import Member, MemberKind, Message
from aidiscussion.services.tokens import estimate_history_tokens, estimate_tokens, trim_history


def make_message(message_id: int, content: str) -> Message:
    sender = Member(id="ai", name="顾问", kind=MemberKind.AI)
    return Message(
        id=message_id,
        conversation_id="c1",
        sender_id=sender.id,
        sender=sender,
        content=content,
    )


def test_estimate_tokens_counts_cjk_and_ascii() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("你好世界") == 4
    assert estimate_tokens("hello world") == 3  # 11 个字符按 4:1 向上取整
    assert estimate_tokens("你好 hello") == 4  # 2 个汉字 + 6 个字符


def test_estimate_history_tokens_includes_overhead() -> None:
    history = [make_message(1, "你好"), make_message(2, "在吗")]

    assert estimate_history_tokens(history) == estimate_tokens("你好") * 2 + 8


def test_trim_history_drops_oldest_first() -> None:
    history = [
        make_message(1, "第一条消息"),
        make_message(2, "第二条消息"),
        make_message(3, "第三条消息"),
    ]

    trimmed = trim_history(history, estimate_history_tokens(history) - 1)

    assert [item.id for item in trimmed] == [2, 3]


def test_trim_history_keeps_at_least_one_message() -> None:
    history = [make_message(1, "很长" * 100), make_message(2, "也很长" * 100)]

    trimmed = trim_history(history, 1)

    assert [item.id for item in trimmed] == [2]
