"""领域模型。

设计要点：群聊和私聊共用同一套表结构，只靠 Conversation.type 区分。
私聊就是「只有你和某个 AI 两个成员的 direct 会话」，因此消息链路只有一条。
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aidiscussion.db.base import Base


def new_id() -> str:
    return uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


def _str_enum(enum_type: type[enum.Enum], name: str) -> Enum:
    """在数据库中以枚举值（human / group）存储，而不是枚举名。"""
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        length=16,
        values_callable=lambda members: [item.value for item in members],
    )


class MemberKind(enum.StrEnum):
    HUMAN = "human"
    AI = "ai"


class ConversationType(enum.StrEnum):
    GROUP = "group"
    DIRECT = "direct"


class MessageStatus(enum.StrEnum):
    SENT = "sent"
    FAILED = "failed"


class Member(Base):
    """会话成员。你（human）和每个 AI 都是同一种实体，只是 kind 不同。"""

    __tablename__ = "members"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[MemberKind] = mapped_column(
        _str_enum(MemberKind, "member_kind"), default=MemberKind.AI
    )
    name: Mapped[str] = mapped_column(String(50), unique=True)
    avatar: Mapped[str] = mapped_column(String(200), default="")
    # 成员绑定的模型厂商（deepseek / kimi / doubao / qwen），密钥来自环境变量
    provider: Mapped[str] = mapped_column(String(20), default="deepseek")
    model: Mapped[str | None] = mapped_column(String(80), default=None)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Provider(Base):
    """模型接入配置。

    内置四家（豆包 / Kimi / DeepSeek / 通义千问）在首次启动时按 .env 播种，
    之后界面里改的就是这份数据；用户也可以直接新增任意 OpenAI 兼容服务。
    """

    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    label: Mapped[str] = mapped_column(String(50))
    base_url: Mapped[str] = mapped_column(String(200))
    model: Mapped[str] = mapped_column(String(80))
    # 本地桌面应用，密钥跟着数据库走；接口返回时只给脱敏提示
    api_key: Mapped[str] = mapped_column(String(300), default="")
    # Kimi 等部分模型只接受 temperature=1，这类厂商不发热量参数
    send_temperature: Mapped[bool] = mapped_column(Boolean, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Conversation(Base):
    """会话：群聊或私聊。"""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    type: Mapped[ConversationType] = mapped_column(_str_enum(ConversationType, "conversation_type"))
    title: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    member_links: Mapped[list[ConversationMember]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        lazy="selectin",
        # 同一批入群的成员 joined_at 可能完全相同，用 member_id 兜底保证顺序稳定
        order_by=lambda: (ConversationMember.joined_at, ConversationMember.member_id),
    )


class ConversationMember(Base):
    """会话与成员的关联。踢人就是删除一行，历史消息仍然保留。"""

    __tablename__ = "conversation_members"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), primary_key=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="member_links")
    member: Mapped[Member] = relationship(lazy="selectin")


class Message(Base):
    """消息。所有消息统一存储，不区分群聊私聊。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[str] = mapped_column(ForeignKey("members.id"))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[MessageStatus] = mapped_column(
        _str_enum(MessageStatus, "message_status"), default=MessageStatus.SENT
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sender: Mapped[Member] = relationship(lazy="joined")


class ConversationSummary(Base):
    """一段历史的压缩摘要。

    up_to_message_id 表示「消息 id <= 该值的部分已经被摘要覆盖」，
    组装上下文时只需取它之后的消息，避免重复把老内容塞给模型。
    """

    __tablename__ = "conversation_summaries"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[str] = mapped_column(Text, default="")
    up_to_message_id: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Memory(Base):
    """长期记忆：关于用户、跨会话有效的事实与偏好。

    不绑定具体成员：群里的 AI 共享同一份「关于你的记忆」，
    这样从群聊里记下的信息，私聊时同样能被用上。
    """

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(String(200))
    # 来源会话被删除时记忆仍然保留，所以用 SET NULL
    source_conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
