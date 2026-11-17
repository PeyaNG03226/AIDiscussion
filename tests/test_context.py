"""上下文字符串拼装测试。"""

from aidiscussion.db.models import Member, MemberKind, Message
from aidiscussion.services.context import build_prompt


def make_member(member_id: str, name: str, *, kind=MemberKind.AI, prompt: str = "") -> Member:
    return Member(id=member_id, name=name, kind=kind, system_prompt=prompt, temperature=0.7)


def make_message(
    message_id: int, sender: Member, content: str, *, conversation_id: str = "c1"
) -> Message:
    return Message(
        id=message_id,
        conversation_id=conversation_id,
        sender_id=sender.id,
        sender=sender,
        content=content,
    )


def test_direct_chat_has_no_name_prefix() -> None:
    me = make_member("me", "我", kind=MemberKind.HUMAN)
    ai = make_member("ai", "私人助理", prompt="你是效率至上的私人助理")
    history = [make_message(1, me, "帮我排个计划")]

    prompt = build_prompt(member=ai, history=history, roster=[me, ai], is_group=False)

    assert "效率至上的私人助理" in prompt[0].content
    assert prompt[1].role == "user"
    assert prompt[1].content == "帮我排个计划"


def test_group_chat_marks_speaker_and_own_messages() -> None:
    me = make_member("me", "我", kind=MemberKind.HUMAN)
    lawyer = make_member("lawyer", "法务", prompt="你是严谨的法务")
    agent = make_member("agent", "地产顾问", prompt="你是资深地产顾问")
    history = [
        make_message(1, me, "这个合同能签吗"),
        make_message(2, lawyer, "先看违约金条款"),
        make_message(3, agent, "我觉得价格还能再谈"),
    ]

    prompt = build_prompt(
        member=lawyer, history=history, roster=[me, lawyer, agent], is_group=True
    )

    system = prompt[0].content
    assert "多人群聊" in system
    assert "地产顾问" in system
    assert "法务" in system
    assert prompt[1].content == "我：这个合同能签吗"
    assert prompt[2].role == "assistant"
    assert prompt[2].content == "先看违约金条款"
    assert prompt[3].content == "地产顾问：我觉得价格还能再谈"


def test_empty_persona_falls_back_to_generated_identity() -> None:
    ai = make_member("ai", "新人", prompt="  ")
    prompt = build_prompt(member=ai, history=[], roster=[ai], is_group=False)

    assert "新人" in prompt[0].content


def test_other_conversation_context_is_injected() -> None:
    "私聊时要能看到群里刚刚聊的内容，否则用户一换会话 AI 就失忆。"
    me = make_member("me", "我", kind=MemberKind.HUMAN)
    ai = make_member("ai", "豆包", prompt="你是豆包")
    group_message = make_message(7, me, "小草连在一起 打一字", conversation_id="group-1")

    prompt = build_prompt(
        member=ai,
        history=[make_message(9, me, "刚刚我出的题还记得吗")],
        roster=[me, ai],
        is_group=False,
        other_context=[(group_message, "AI 茶话会")],
    )

    system = prompt[0].content
    assert "其他会话里的最近动态" in system
    assert "[AI 茶话会] 我：小草连在一起 打一字" in system


def test_summary_and_memories_are_injected_into_system_prompt() -> None:
    me = make_member("me", "我", kind=MemberKind.HUMAN)
    ai = make_member("ai", "私人助理", prompt="你是私人助理")

    prompt = build_prompt(
        member=ai,
        history=[make_message(9, me, "继续")],
        roster=[me, ai],
        is_group=False,
        summary="之前聊过装修预算，定在 20 万",
        memories=["用户喜欢简洁的回答", "用户住在杭州"],
    )

    system = prompt[0].content
    assert "更早的对话已经被压缩" in system
    assert "装修预算" in system
    assert "用户喜欢简洁的回答" in system
    assert "用户住在杭州" in system
    assert prompt[1].content == "继续"
