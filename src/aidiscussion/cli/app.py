"""终端客户端：把仓储、编排和模型接入串成可用的聊天程序。"""

import asyncio
import sys
from collections.abc import Callable
from enum import StrEnum

from colorama import Fore, Style, just_fix_windows_console
from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.config import get_settings
from aidiscussion.db.database import Database, get_database
from aidiscussion.db.models import Conversation, ConversationType, MemberKind, MessageStatus
from aidiscussion.llm.base import LLMClient
from aidiscussion.llm.factory import get_llm
from aidiscussion.repositories import conversations as conv_repo
from aidiscussion.repositories import members as member_repo
from aidiscussion.repositories import messages as message_repo
from aidiscussion.services import chat
from aidiscussion.services.chat import ReplyDelta, ReplyDone, ReplyFailed, ReplyStart

WELCOME = """AIDiscussion 终端版
输入内容即可发言；/help 查看命令，/exit 退出。
"""

HELP = """可用命令：
  /list               查看所有会话
  /switch <序号>      切换会话
  /new <群名>         新建群聊（自动拉入全部 AI 成员）
  /dm <名字>          打开与某个 AI 的私聊
  /members            查看当前会话成员
  /create <名字> [人设] 新建一个 AI 成员
  /add <名字>         把 AI 成员拉进当前群
  /kick <名字>        把 AI 成员移出当前群
  /mode <模式>        群聊模式：mention / free / supervisor
  /history [条数]     查看最近消息
  /help               显示本帮助
  /exit               退出
"""

DEMO_MEMBERS = [
    ("通用助手", "你是通用助手，回答简洁、给可执行建议，不空泛。"),
    ("行业顾问", "你是资深行业顾问，回答有条理，主动指出风险与前提条件。"),
    ("毒舌朋友", "你是毒舌但真诚的朋友，说话直接，敢指出问题，不用客套话。"),
]


class Mode(StrEnum):
    MENTION = "mention"
    FREE = "free"
    SUPERVISOR = "supervisor"


MODE_LABELS = {
    Mode.MENTION: "@ 点名",
    Mode.FREE: "自由讨论",
    Mode.SUPERVISOR: "主持人调度",
}


class ChatApp:
    def __init__(
        self,
        *,
        db: Database,
        llm: LLMClient,
        out: Callable[[str], None] = print,
        write: Callable[[str], None] | None = None,
        color: bool = False,
        history_limit: int = 20,
        max_replies: int = 4,
    ) -> None:
        self._db = db
        self._llm = llm
        self._out = out
        self._writer = write or self._stdout_write
        self._color = color
        self._history_limit = history_limit
        self._max_replies = max_replies

        self.me_id: str | None = None
        self.me_name = "我"
        self.conversation_id: str | None = None
        self.conversation_title = ""
        self.mode = Mode.MENTION

    # ---------- 启动 ----------

    async def bootstrap(self) -> None:
        async with self._db.session() as session:
            me = await member_repo.get_self_member(session)
            self.me_id = me.id
            self.me_name = me.name

            ai_members = await member_repo.list_members(session, kind=MemberKind.AI)
            if not ai_members:
                ai_members = [
                    await member_repo.create_member(session, name=name, system_prompt=prompt)
                    for name, prompt in DEMO_MEMBERS
                ]

            conversations = await conv_repo.list_conversations(session)
            if not conversations:
                conversation = await conv_repo.create_conversation(
                    session,
                    type=ConversationType.GROUP,
                    title="随便聊聊",
                    member_ids=[me.id, *[member.id for member in ai_members]],
                )
            else:
                conversation = conversations[0]

            self._enter(conversation)

    def _enter(self, conversation: Conversation) -> None:
        self.conversation_id = conversation.id
        default_title = "私聊" if conversation.type is ConversationType.DIRECT else "群聊"
        self.conversation_title = conversation.title or default_title

    # ---------- 主循环 ----------

    async def run(self) -> None:
        await self.bootstrap()
        self._out(WELCOME)
        while True:
            try:
                line = await asyncio.to_thread(input, self._prompt())
            except (EOFError, KeyboardInterrupt):
                self._out("")
                break

            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if not await self.handle_command(line):
                    break
            else:
                await self.send(line)

    def _prompt(self) -> str:
        label = MODE_LABELS[self.mode] if self.conversation_title else ""
        return f"{self.conversation_title}（{label}）› "

    # ---------- 命令 ----------

    async def handle_command(self, line: str) -> bool:
        command, _, argument = line[1:].partition(" ")
        command = command.lower()
        argument = argument.strip()

        handlers = {
            "help": self._cmd_help,
            "list": self._cmd_list,
            "switch": self._cmd_switch,
            "new": self._cmd_new,
            "dm": self._cmd_dm,
            "members": self._cmd_members,
            "create": self._cmd_create,
            "add": self._cmd_add,
            "kick": self._cmd_kick,
            "mode": self._cmd_mode,
            "history": self._cmd_history,
        }

        if command in ("exit", "quit"):
            return False
        handler = handlers.get(command)
        if handler is None:
            self._out(f"未知命令：/{command}，输入 /help 查看可用命令")
            return True
        await handler(argument)
        return True

    async def _cmd_help(self, _argument: str) -> None:
        self._out(HELP)

    async def _cmd_list(self, _argument: str) -> None:
        async with self._db.session() as session:
            conversations = await conv_repo.list_conversations(session)
            if not conversations:
                self._out("还没有会话，用 /new 建一个群")
                return
            for index, conversation in enumerate(conversations, start=1):
                mark = "*" if conversation.id == self.conversation_id else " "
                kind = "私聊" if conversation.type is ConversationType.DIRECT else "群聊"
                names = "、".join(link.member.name for link in conversation.member_links)
                self._out(f"{mark} {index}. [{kind}] {conversation.title}（{names}）")

    async def _cmd_switch(self, argument: str) -> None:
        try:
            index = int(argument) - 1
        except ValueError:
            self._out("用法：/switch <序号>，序号来自 /list")
            return

        async with self._db.session() as session:
            conversations = await conv_repo.list_conversations(session)
            if not 0 <= index < len(conversations):
                self._out("序号超出范围")
                return
            self._enter(conversations[index])
            self._out(f"已切换到「{self.conversation_title}」")

    async def _cmd_new(self, argument: str) -> None:
        title = argument or "新群聊"
        async with self._db.session() as session:
            me = await member_repo.get_member(session, self._require_me())
            ai_members = await member_repo.list_members(session, kind=MemberKind.AI)
            conversation = await conv_repo.create_conversation(
                session,
                type=ConversationType.GROUP,
                title=title,
                member_ids=[me.id, *[member.id for member in ai_members]],
            )
            self._enter(conversation)
            self._out(f"已创建群「{title}」，成员 {len(ai_members) + 1} 人")

    async def _cmd_dm(self, argument: str) -> None:
        if not argument:
            self._out("用法：/dm <名字>")
            return

        async with self._db.session() as session:
            me = await member_repo.get_member(session, self._require_me())
            ai = await member_repo.get_member_by_name(session, argument)
            if ai is None or not ai.is_active or ai.kind is not MemberKind.AI:
                self._out(f"没有找到可私聊的 AI 成员：{argument}")
                return

            conversation = await conv_repo.get_direct_conversation(session, me.id, ai.id)
            if conversation is None:
                conversation = await conv_repo.create_conversation(
                    session,
                    type=ConversationType.DIRECT,
                    title=f"与 {ai.name} 的私聊",
                    member_ids=[me.id, ai.id],
                )
            self._enter(conversation)
            self._out(f"已打开与「{ai.name}」的私聊")

    async def _cmd_members(self, _argument: str) -> None:
        async with self._db.session() as session:
            conversation = await self._current(session)
            if conversation is None:
                return
            names = [link.member.name for link in conversation.member_links]
            self._out("成员：" + "、".join(names))

    async def _cmd_create(self, argument: str) -> None:
        name, _, persona = argument.partition(" ")
        if not name:
            self._out("用法：/create <名字> [人设]")
            return

        async with self._db.session() as session:
            try:
                member = await member_repo.create_member(
                    session,
                    name=name,
                    system_prompt=persona.strip(),
                )
            except ValueError as exc:
                self._out(str(exc))
                return

            self._out(f"已创建 AI 成员「{member.name}」")
            conversation = await self._current(session)
            if conversation is not None and conversation.type is ConversationType.GROUP:
                await conv_repo.add_member(session, conversation.id, member.id)
                self._out(f"已把「{member.name}」拉进「{self.conversation_title}」")

    async def _cmd_add(self, argument: str) -> None:
        if not argument:
            self._out("用法：/add <名字>")
            return

        async with self._db.session() as session:
            conversation = await self._current(session)
            if conversation is None:
                return
            if conversation.type is ConversationType.DIRECT:
                self._out("私聊只有两个人，不能再加成员")
                return

            member = await member_repo.get_member_by_name(session, argument)
            if member is None or not member.is_active:
                self._out(f"没有找到成员：{argument}")
                return

            added = await conv_repo.add_member(session, conversation.id, member.id)
            self._out(f"已把「{member.name}」拉进群" if added else f"「{member.name}」已经在群里了")

    async def _cmd_kick(self, argument: str) -> None:
        if not argument:
            self._out("用法：/kick <名字>")
            return

        async with self._db.session() as session:
            conversation = await self._current(session)
            if conversation is None:
                return
            if conversation.type is ConversationType.DIRECT:
                self._out("私聊不能踢人")
                return

            member = await member_repo.get_member_by_name(session, argument)
            if member is None or member.kind is MemberKind.HUMAN:
                self._out(f"没有找到群里的 AI 成员：{argument}")
                return

            removed = await conv_repo.remove_member(session, conversation.id, member.id)
            self._out(f"已把「{member.name}」移出群" if removed else f"「{member.name}」不在群里")

    async def _cmd_mode(self, argument: str) -> None:
        try:
            mode = Mode(argument.lower())
        except ValueError:
            options = " / ".join(item.value for item in Mode)
            self._out(f"模式无效，可选：{options}")
            return

        self.mode = mode
        self._out(f"群聊模式已切换为：{MODE_LABELS[mode]}")

    async def _cmd_history(self, argument: str) -> None:
        try:
            limit = int(argument) if argument else 20
        except ValueError:
            self._out("用法：/history [条数]")
            return

        async with self._db.session() as session:
            conversation = await self._current(session)
            if conversation is None:
                return
            history = await message_repo.list_messages(
                session, conversation.id, limit=max(1, min(limit, 200))
            )
            if not history:
                self._out("还没有消息")
                return
            for message in history:
                suffix = "（发送失败）" if message.status is MessageStatus.FAILED else ""
                self._out(f"{message.sender.name}：{message.content}{suffix}")

    # ---------- 发言 ----------

    async def send(self, text: str) -> None:
        async with self._db.session() as session:
            conversation = await self._current(session)
            if conversation is None:
                return

            message = await chat.post_user_message(
                session,
                conversation_id=conversation.id,
                sender_id=self._require_me(),
                content=text,
            )

            printed_header = False
            async for event in chat.generate_replies(
                session,
                self._llm,
                conversation=conversation,
                user_message=message,
                mode=self.mode.value,
                max_replies=self._max_replies,
                history_limit=self._history_limit,
            ):
                if isinstance(event, ReplyStart):
                    self._write("\n")
                    self._write_name(event.member.name)
                    printed_header = True
                elif isinstance(event, ReplyDelta):
                    self._write(event.text)
                elif isinstance(event, ReplyDone):
                    self._write("\n")
                elif isinstance(event, ReplyFailed):
                    if not printed_header:
                        self._write("\n")
                    self._write_error(f"[回复中断] {event.error}\n")
                    printed_header = False

    # ---------- 辅助 ----------

    def _require_me(self) -> str:
        if self.me_id is None:
            raise RuntimeError("尚未初始化当前用户")
        return self.me_id

    async def _current(self, session: AsyncSession) -> Conversation | None:
        if self.conversation_id is None:
            self._out("还没有选择会话，用 /new 建一个群")
            return None
        conversation = await conv_repo.get_conversation(session, self.conversation_id)
        if conversation is None:
            self._out("当前会话已不存在")
            self.conversation_id = None
            return None
        self._enter(conversation)
        return conversation

    @staticmethod
    def _stdout_write(text: str) -> None:
        print(text, end="", flush=True)

    def _write(self, text: str) -> None:
        self._writer(text)

    def _write_name(self, name: str) -> None:
        if self._color:
            self._write(f"{Fore.CYAN}{name}{Style.RESET_ALL}：")
        else:
            self._write(f"{name}：")

    def _write_error(self, text: str) -> None:
        if self._color:
            self._write(f"{Fore.RED}{text}{Style.RESET_ALL}")
        else:
            self._write(text)


async def run_chat() -> int:
    settings = get_settings()
    settings.ensure_data_dir()

    color = sys.stdout.isatty()
    if color:
        just_fix_windows_console()

    db = get_database()
    await db.create_all()
    llm = get_llm()
    app = ChatApp(db=db, llm=llm, out=print, color=color)
    try:
        await app.run()
    finally:
        await llm.aclose()
        await db.dispose()
    return 0


def start() -> int:
    return asyncio.run(run_chat())
