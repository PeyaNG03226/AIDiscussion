"""上下文组装。

把数据库里的消息翻译成模型能理解的消息列表，并处理两件容易出错的细节：
1. 群聊时必须让 AI 知道「谁在说话」，否则多个成员会张冠李戴；
2. 自己在历史里的发言要标记为 assistant，否则模型会把自己过去的话当成别人说的。
"""

from collections.abc import Sequence

from aidiscussion.db.models import Member, Message
from aidiscussion.llm.base import LLMMessage


def build_prompt(
    *,
    member: Member,
    history: Sequence[Message],
    roster: Sequence[Member],
    is_group: bool,
    summary: str | None = None,
    memories: Sequence[str] = (),
) -> list[LLMMessage]:
    persona = member.system_prompt.strip() or f"你是「{member.name}」，一个有主见的对话伙伴。"

    system_parts = [persona]
    if is_group:
        others = "、".join(item.name for item in roster if item.id != member.id)
        system_parts.append(
            f"这是一个多人群聊，其他成员：{others}。你的名字是「{member.name}」。"
            "直接说你要说的话，不要加名字前缀，也不要复述别人的整段内容。"
        )
    else:
        system_parts.append("这是你和用户的私聊，直接回答，不要加名字前缀。")

    if memories:
        bullet = "\n".join(f"- {item}" for item in memories)
        system_parts.append(
            "关于用户的长期记忆（可能有用，相关时自然带入，不要逐条复述）：\n" + bullet
        )

    if summary:
        system_parts.append(f"更早的对话已经被压缩成下面的摘要：\n{summary}")

    messages = [LLMMessage(role="system", content="\n\n".join(system_parts))]
    for item in history:
        if item.sender_id == member.id:
            messages.append(LLMMessage(role="assistant", content=item.content))
        elif is_group:
            messages.append(LLMMessage(role="user", content=f"{item.sender.name}：{item.content}"))
        else:
            messages.append(LLMMessage(role="user", content=item.content))
    return messages
