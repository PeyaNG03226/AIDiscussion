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
    model: Mapped[str | None] = mapped_column(String(80), default=None)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
