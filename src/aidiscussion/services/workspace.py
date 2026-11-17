"""首次启动的工作区初始化：示例 AI 成员与默认群聊。

成员按「不同厂商的大模型」预置：每个成员绑定一个 provider，
密钥来自环境变量，因此豆包 / Kimi / DeepSeek / 通义千问可以同群对话。
命令行和 Web 服务共用这段逻辑，保证两种入口看到的是同一份数据。
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.db.models import Conversation, ConversationType, Member, MemberKind
from aidiscussion.repositories import conversations as conv_repo
from aidiscussion.repositories import members as member_repo


@dataclass(frozen=True, slots=True)
class DemoMember:
    name: str
    provider: str
    prompt: str


DEMO_MEMBERS: list[DemoMember] = [
    DemoMember(
        "豆包",
        "doubao",
        "你是豆包（Doubao），字节跳动出品的大模型。回答直接、口语化，先给结论再补理由。",
    ),
    DemoMember(
        "Kimi",
        "kimi",
        "你是 Kimi，月之暗面出品的大模型。擅长长文本与资料整理，回答条理清晰、善于归纳。",
    ),
    DemoMember(
        "DeepSeek",
        "deepseek",
        "你是 DeepSeek，深度求索出品的大模型。擅长推理与代码，回答严谨、习惯拆步骤。",
    ),
    DemoMember(
        "通义千问",
        "qwen",
        "你是通义千问（Qwen），阿里出品的大模型。知识面广，回答平实，乐于补充背景。",
    ),
]

# 旧版本按人设命名的成员：就地改名升级，保留成员 id，聊天记录不丢
LEGACY_MEMBER_TARGETS: dict[str, str] = {
    "通用助手": "豆包",
    "行业顾问": "Kimi",
    "毒舌朋友": "DeepSeek",
}


def _demo_by_name() -> dict[str, DemoMember]:
    return {demo.name: demo for demo in DEMO_MEMBERS}


async def ensure_workspace(session: AsyncSession) -> Conversation:
    """保证至少有一个 AI 成员和一个群聊，返回当前应打开的会话。"""
    me = await member_repo.get_self_member(session)

    ai_members = await member_repo.list_members(session, kind=MemberKind.AI)
    if await _upgrade_legacy_members(session, ai_members):
        # 旧工作区只有三个人设成员，把后来新增的厂商成员补齐
        await _create_missing_demo_members(session)
        ai_members = await member_repo.list_members(session, kind=MemberKind.AI)
    elif not ai_members:
        ai_members = [await _create_demo_member(session, demo) for demo in DEMO_MEMBERS]
    await _rename_legacy_direct_titles(session)

    conversations = await conv_repo.list_conversations(session)
    if conversations:
        return conversations[0]

    return await conv_repo.create_conversation(
        session,
        type=ConversationType.GROUP,
        title="随便聊聊",
        member_ids=[me.id, *[member.id for member in ai_members]],
    )


async def _create_demo_member(session: AsyncSession, demo: DemoMember) -> Member:
    return await member_repo.create_member(
        session,
        name=demo.name,
        provider=demo.provider,
        system_prompt=demo.prompt,
    )


async def _create_missing_demo_members(session: AsyncSession) -> None:
    for demo in DEMO_MEMBERS:
        if await member_repo.get_member_by_name(session, demo.name) is None:
            await _create_demo_member(session, demo)


async def _upgrade_legacy_members(session: AsyncSession, ai_members: list[Member]) -> bool:
    """把旧的人设成员改名成厂商成员；目标名字已被占用时跳过。"""
    demos = _demo_by_name()
    upgraded = False
    for member in ai_members:
        target_name = LEGACY_MEMBER_TARGETS.get(member.name)
        demo = demos.get(target_name) if target_name else None
        if demo is None:
            continue
        if await member_repo.get_member_by_name(session, demo.name) is not None:
            continue
        member.name = demo.name
        member.provider = demo.provider
        member.model = None  # 用厂商默认模型
        member.system_prompt = demo.prompt
        upgraded = True
    if upgraded:
        await session.flush()
    return upgraded


async def _rename_legacy_direct_titles(session: AsyncSession) -> None:
    """私聊标题是存下来的字符串：成员改过名后把它一起改掉。"""
    changed = False
    for conversation in await conv_repo.list_conversations(session):
        for legacy, new_name in LEGACY_MEMBER_TARGETS.items():
            if conversation.title == f"与 {legacy} 的私聊":
                conversation.title = f"与 {new_name} 的私聊"
                changed = True
    if changed:
        await session.flush()
