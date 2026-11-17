"""API 出入参与序列化。"""

from datetime import datetime

from pydantic import BaseModel, Field

from aidiscussion.config import DEFAULT_PROVIDER
from aidiscussion.db.models import Conversation, Member, Message
from aidiscussion.llm import registry


class MemberOut(BaseModel):
    id: str
    name: str
    kind: str
    avatar: str
    provider: str
    provider_label: str
    model: str | None
    system_prompt: str
    temperature: float
    is_active: bool


class MemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    provider: str = DEFAULT_PROVIDER
    system_prompt: str = ""
    model: str | None = None
    temperature: float = Field(default=0.7, ge=0, le=2)


class MemberUpdate(BaseModel):
    provider: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)


class ProviderOut(BaseModel):
    id: str
    label: str
    base_url: str
    model: str
    has_key: bool
    api_key_hint: str
    is_builtin: bool


class ProviderCreate(BaseModel):
    label: str = Field(min_length=1, max_length=30)
    base_url: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=80)
    api_key: str = ""
    send_temperature: bool = True


class ProviderUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=30)
    base_url: str | None = Field(default=None, min_length=1, max_length=200)
    model: str | None = Field(default=None, min_length=1, max_length=80)
    # 传空字符串表示清空密钥，传 None 表示保持原样
    api_key: str | None = None
    send_temperature: bool | None = None


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
        provider=member.provider,
        provider_label=registry.label_for(member.provider),
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
