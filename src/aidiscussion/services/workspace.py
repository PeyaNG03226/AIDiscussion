"""首次启动的工作区初始化：示例 AI 成员与默认群聊。

命令行和 Web 服务共用这段逻辑，保证两种入口看到的是同一份数据。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.db.models import Conversation, ConversationType, MemberKind
from aidiscussion.repositories import conversations as conv_repo
from aidiscussion.repositories import members as member_repo

DEMO_MEMBERS = [
    ("通用助手", "你是通用助手，回答简洁、给可执行建议，不空泛。"),
    ("行业顾问", "你是资深行业顾问，回答有条理，主动指出风险与前提条件。"),
    ("毒舌朋友", "你是毒舌但真诚的朋友，说话直接，敢指出问题，不用客套话。"),
]


async def ensure_workspace(session: AsyncSession) -> Conversation:
    """保证至少有一个 AI 成员和一个群聊，返回当前应打开的会话。"""
    me = await member_repo.get_self_member(session)

    ai_members = await member_repo.list_members(session, kind=MemberKind.AI)
    if not ai_members:
        ai_members = [
            await member_repo.create_member(session, name=name, system_prompt=prompt)
            for name, prompt in DEMO_MEMBERS
        ]

    conversations = await conv_repo.list_conversations(session)
    if conversations:
        return conversations[0]

    return await conv_repo.create_conversation(
        session,
        type=ConversationType.GROUP,
        title="随便聊聊",
        member_ids=[me.id, *[member.id for member in ai_members]],
    )
