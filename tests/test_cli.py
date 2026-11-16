"""终端客户端测试：命令、成员管理和消息闭环。"""

import re
from collections.abc import AsyncIterator, Sequence

from aidiscussion.cli.app import ChatApp, Mode
from aidiscussion.db.database import Database
from aidiscussion.db.models import MemberKind
from aidiscussion.llm.base import LLMMessage
from aidiscussion.repositories import conversations as conv_repo
from aidiscussion.repositories import members as member_repo
from aidiscussion.repositories import messages as message_repo


class FakeLLM:
    def __init__(self, reply: str = "收到") -> None:
        self.reply = reply
        self.calls: list[list[LLMMessage]] = []

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        self.calls.append(list(messages))
        for char in self.reply:
            yield char

    async def aclose(self) -> None:
        return None


async def make_app(db: Database) -> tuple[ChatApp, list[str], list[str], FakeLLM]:
    out: list[str] = []
    written: list[str] = []
    llm = FakeLLM()
    app = ChatApp(
        db=db,
        llm=llm,
        out=out.append,
        write=written.append,
        history_limit=20,
        max_replies=3,
    )
    await app.bootstrap()
    return app, out, written, llm


async def test_bootstrap_creates_demo_workspace(db: Database) -> None:
    app, _, _, _ = await make_app(db)

    async with db.session() as session:
        ai_members = await member_repo.list_members(session, kind=MemberKind.AI)
        conversations = await conv_repo.list_conversations(session)
        me = await member_repo.get_self_member(session)

    assert len(ai_members) == 3
    assert len(conversations) == 1
    assert app.me_id == me.id
    assert app.conversation_id == conversations[0].id


async def test_create_and_kick_member(db: Database) -> None:
    app, out, _, _ = await make_app(db)

    await app.handle_command("/create 财务 你是严谨的财务")
    async with db.session() as session:
        member = await member_repo.get_member_by_name(session, "财务")
        conversation = await conv_repo.get_conversation(session, app.conversation_id)
        assert member is not None
        assert member.id in {link.member_id for link in conversation.member_links}

    await app.handle_command("/kick 财务")
    async with db.session() as session:
        conversation = await conv_repo.get_conversation(session, app.conversation_id)
        assert "财务" not in {link.member.name for link in conversation.member_links}

    assert any("拉进" in line for line in out)
    assert any("移出" in line for line in out)


async def test_direct_chat_is_reused(db: Database) -> None:
    app, _, _, _ = await make_app(db)

    await app.handle_command("/dm 通用助手")
    first = app.conversation_id

    await app.handle_command("/new 另一个群")
    await app.handle_command("/dm 通用助手")

    assert app.conversation_id == first


async def test_free_mode_gets_replies_from_all_members(db: Database) -> None:
    app, _, _, llm = await make_app(db)

    await app.handle_command("/mode free")
    await app.send("大家好")

    assert len(llm.calls) == 3
    async with db.session() as session:
        history = await message_repo.list_messages(session, app.conversation_id)
    assert len(history) == 4
    assert history[0].sender_id == app.me_id


async def test_mention_mode_stays_silent_without_mention(db: Database) -> None:
    app, _, _, llm = await make_app(db)

    await app.send("大家好")

    assert llm.calls == []
    async with db.session() as session:
        history = await message_repo.list_messages(session, app.conversation_id)
    assert len(history) == 1


async def test_mention_mode_answers_named_member(db: Database) -> None:
    app, _, written, _ = await make_app(db)

    await app.send("@行业顾问 这单怎么谈")

    async with db.session() as session:
        history = await message_repo.list_messages(session, app.conversation_id)
    assert len(history) == 2
    assert "行业顾问：" in "".join(written)


async def test_switch_between_conversations(db: Database) -> None:
    app, out, _, _ = await make_app(db)
    first = app.conversation_id

    await app.handle_command("/new 第二个群")
    second = app.conversation_id
    assert second != first

    # 两个群的创建时间可能相同，索引顺序不稳定，因此从 /list 输出里读实际序号
    await app.handle_command("/list")
    line = next(item for item in out if "随便聊聊" in item)
    index = re.search(r"(\d+)\.", line)
    assert index is not None

    await app.handle_command(f"/switch {index.group(1)}")

    assert app.conversation_id == first
    assert any("已切换" in item for item in out)


async def test_invalid_mode_is_rejected(db: Database) -> None:
    app, out, _, _ = await make_app(db)

    await app.handle_command("/mode 乱写")

    assert app.mode is Mode.MENTION
    assert any("模式无效" in line for line in out)


async def test_direct_chat_replies_without_mention(db: Database) -> None:
    app, _, _, _ = await make_app(db)

    await app.handle_command("/dm 毒舌朋友")
    await app.send("你觉得我该辞职吗")

    async with db.session() as session:
        history = await message_repo.list_messages(session, app.conversation_id)
    assert len(history) == 2
    assert history[1].sender_id != app.me_id


async def test_history_and_exit(db: Database) -> None:
    app, out, _, _ = await make_app(db)

    await app.handle_command("/dm 通用助手")
    await app.send("记住这句话")
    await app.handle_command("/history 5")

    assert any("记住这句话" in line for line in out)
    assert await app.handle_command("/exit") is False
