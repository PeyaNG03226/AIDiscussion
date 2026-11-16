"""API 出入参与序列化。"""

from datetime import datetime

from pydantic import BaseModel, Field

from aidiscussion.db.models import Conversation, Member, Message


class MemberOut(BaseModel):
    id: str
    name: str
    kind: str
    avatar: str
    model: str | None
    system_prompt: str
    temperature: float
    is_active: bool


class MemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    system_prompt: str = ""
    model: str | None = None
    temperature: float = Field(default=0.7, ge=0, le=2)


class MemberUpdate(BaseModel):
    system_prompt: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)


class MessageOut(BaseModel):
    id: int
    conversation_id: str
    sender_id: str
    sender_name: str
    content: str
    status: str
    created_at: datetime | None


class ConversationCreate(BaseModel):
    title: str = ""
    member_ids: list[str] = Field(default_factory=list)


class DirectCreate(BaseModel):
    member_id: str


class MemberRef(BaseModel):
    member_id: str


class MemberAdded(BaseModel):
    added: bool


class ConversationOut(BaseModel):
    id: str
    type: str
    title: str
    last_message_at: datetime | None
    members: list[MemberOut]
    last_message: MessageOut | None


def member_out(member: Member) -> MemberOut:
    return MemberOut(
        id=member.id,
        name=member.name,
        kind=member.kind.value,
        avatar=member.avatar,
        model=member.model,
        system_prompt=member.system_prompt,
        temperature=member.temperature,
        is_active=member.is_active,
    )


def message_out(message: Message, *, sender_name: str | None = None) -> MessageOut:
    return MessageOut(
        id=message.id,
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
        sender_name=sender_name or message.sender.name,
        content=message.content,
        status=message.status.value,
        created_at=message.created_at,
    )


def conversation_out(
    conversation: Conversation, *, last_message: MessageOut | None = None
) -> ConversationOut:
    return ConversationOut(
        id=conversation.id,
        type=conversation.type.value,
        title=conversation.title,
        last_message_at=conversation.last_message_at,
        members=[member_out(link.member) for link in conversation.member_links],
        last_message=last_message,
    )
