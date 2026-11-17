"""REST 接口：成员与会话管理。消息收发走 WebSocket。"""

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.api.schemas import (
    ConversationCreate,
    ConversationOut,
    DirectCreate,
    MemberAdded,
    MemberCreate,
    MemberOut,
    MemberRef,
    MemberUpdate,
    MessageOut,
    ProviderCreate,
    ProviderOut,
    ProviderUpdate,
    conversation_out,
    member_out,
    message_out,
)
from aidiscussion.config import get_settings
from aidiscussion.db.models import ConversationType, MemberKind
from aidiscussion.llm import factory, registry
from aidiscussion.repositories import conversations as conv_repo
from aidiscussion.repositories import members as member_repo
from aidiscussion.repositories import messages as message_repo
from aidiscussion.repositories import providers as provider_repo

router = APIRouter(prefix="/api")


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.session() as session:
        yield session


async def _detail(
    session: AsyncSession, conversation_id: str
) -> ConversationOut:
    conversation = await conv_repo.get_conversation(session, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    recent = await message_repo.list_messages(session, conversation.id, limit=1)
    last = message_out(recent[-1]) if recent else None
    return conversation_out(conversation, last_message=last)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/me", response_model=MemberOut)
async def get_me(session: AsyncSession = Depends(get_session)) -> MemberOut:
    return member_out(await member_repo.get_self_member(session))


def _key_hint(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:4]}…{api_key[-4:]}"


def _provider_out(row) -> ProviderOut:
    return ProviderOut(
        id=row.id,
        label=row.label,
        base_url=row.base_url,
        model=row.model,
        has_key=bool(row.api_key),
        api_key_hint=_key_hint(row.api_key),
        is_builtin=row.is_builtin,
    )


def _clean_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="接口地址需要以 http:// 或 https:// 开头")
    return base_url


def _builtin_out(spec) -> ProviderOut:
    secret = spec.api_key.get_secret_value() if spec.api_key else ""
    return ProviderOut(
        id=spec.id,
        label=spec.label,
        base_url=spec.base_url,
        model=spec.model,
        has_key=bool(secret),
        api_key_hint=_key_hint(secret),
        is_builtin=True,
    )


async def _provider_exists(session: AsyncSession, provider_id: str) -> bool:
    """数据库里的自定义模型，或 .env 里的内置厂商，都算存在。"""
    if await provider_repo.get_provider(session, provider_id) is not None:
        return True
    return provider_id in {spec.id for spec in get_settings().provider_specs()}


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(session: AsyncSession = Depends(get_session)) -> list[ProviderOut]:
    """模型接入配置：数据库里的（含内置播种）优先，未播种的内置厂商按 .env 补齐。"""
    rows = await provider_repo.list_providers(session)
    known = {row.id for row in rows}
    results = [_provider_out(row) for row in rows]
    results += [
        _builtin_out(spec) for spec in get_settings().provider_specs() if spec.id not in known
    ]
    return results


@router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider(
    payload: ProviderCreate, session: AsyncSession = Depends(get_session)
) -> ProviderOut:
    row = await provider_repo.create_provider(
        session,
        label=payload.label.strip(),
        base_url=_clean_base_url(payload.base_url),
        model=payload.model.strip(),
        api_key=payload.api_key.strip(),
        send_temperature=payload.send_temperature,
    )
    await registry.refresh(session)
    return _provider_out(row)


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: str,
    payload: ProviderUpdate,
    session: AsyncSession = Depends(get_session),
) -> ProviderOut:
    row = await provider_repo.get_provider(session, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")

    if payload.label is not None:
        row.label = payload.label.strip()
    if payload.base_url is not None:
        row.base_url = _clean_base_url(payload.base_url)
    if payload.model is not None:
        row.model = payload.model.strip()
    if payload.api_key is not None:
        row.api_key = payload.api_key.strip()
    if payload.send_temperature is not None:
        row.send_temperature = payload.send_temperature
    await session.flush()

    await factory.invalidate(provider_id)
    await registry.refresh(session)
    return _provider_out(row)


@router.delete("/providers/{provider_id}", response_model=dict[str, bool])
async def delete_provider(
    provider_id: str, session: AsyncSession = Depends(get_session)
) -> dict[str, bool]:
    row = await provider_repo.get_provider(session, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")

    used = await provider_repo.count_members(session, provider_id)
    if used:
        raise HTTPException(
            status_code=400,
            detail=f"还有 {used} 个成员在用这个模型，先给他们换一个再删除",
        )
    await provider_repo.delete_provider(session, row)
    await factory.invalidate(provider_id)
    await registry.refresh(session)
    return {"deleted": True}


@router.get("/members", response_model=list[MemberOut])
async def list_members(session: AsyncSession = Depends(get_session)) -> list[MemberOut]:
    members = await member_repo.list_members(session)
    return [member_out(member) for member in members]


@router.post("/members", response_model=MemberOut, status_code=201)
async def create_member(
    payload: MemberCreate, session: AsyncSession = Depends(get_session)
) -> MemberOut:
    provider = payload.provider.strip().lower()
    if not await _provider_exists(session, provider):
        raise HTTPException(
            status_code=400, detail=f"未知的模型配置：{payload.provider}，请先在「模型配置」里添加"
        )
    try:
        member = await member_repo.create_member(
            session,
            name=payload.name,
            provider=provider,
            system_prompt=payload.system_prompt,
            model=payload.model,
            temperature=payload.temperature,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return member_out(member)


@router.patch("/members/{member_id}", response_model=MemberOut)
async def update_member(
    member_id: str, payload: MemberUpdate, session: AsyncSession = Depends(get_session)
) -> MemberOut:
    member = await member_repo.get_member(session, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    if member.kind is MemberKind.HUMAN:
        raise HTTPException(status_code=400, detail="不能修改用户自身的人设")

    if payload.provider is not None:
        provider = payload.provider.strip().lower()
        if not await _provider_exists(session, provider):
            raise HTTPException(
                status_code=400,
                detail=f"未知的模型配置：{payload.provider}，请先在「模型配置」里添加",
            )
        member.provider = provider
    if payload.system_prompt is not None:
        member.system_prompt = payload.system_prompt
    if payload.model is not None:
        member.model = payload.model or None
    if payload.temperature is not None:
        member.temperature = payload.temperature
    await session.flush()
    return member_out(member)


@router.delete("/members/{member_id}", response_model=MemberOut)
async def deactivate_member(
    member_id: str, session: AsyncSession = Depends(get_session)
) -> MemberOut:
    member = await member_repo.get_member(session, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    if member.kind is MemberKind.HUMAN:
        raise HTTPException(status_code=400, detail="不能停用用户自身")
    await member_repo.deactivate_member(session, member_id)
    return member_out(member)


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    session: AsyncSession = Depends(get_session),
) -> list[ConversationOut]:
    conversations = await conv_repo.list_conversations(session)
    results: list[ConversationOut] = []
    for conversation in conversations:
        recent = await message_repo.list_messages(session, conversation.id, limit=1)
        last = message_out(recent[-1]) if recent else None
        results.append(conversation_out(conversation, last_message=last))
    return results


@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(
    payload: ConversationCreate, session: AsyncSession = Depends(get_session)
) -> ConversationOut:
    me = await member_repo.get_self_member(session)
    if payload.member_ids:
        member_ids = payload.member_ids
    else:
        member_ids = [
            member.id
            for member in await member_repo.list_members(session, kind=MemberKind.AI)
        ]

    conversation = await conv_repo.create_conversation(
        session,
        type=ConversationType.GROUP,
        title=payload.title or "新群聊",
        member_ids=[me.id, *member_ids],
    )
    return await _detail(session, conversation.id)


@router.post("/conversations/direct", response_model=ConversationOut)
async def open_direct(
    payload: DirectCreate, session: AsyncSession = Depends(get_session)
) -> ConversationOut:
    me = await member_repo.get_self_member(session)
    member = await member_repo.get_member(session, payload.member_id)
    if member is None or not member.is_active or member.kind is not MemberKind.AI:
        raise HTTPException(status_code=400, detail="只能和 AI 成员私聊")

    conversation = await conv_repo.get_direct_conversation(session, me.id, member.id)
    if conversation is None:
        conversation = await conv_repo.create_conversation(
            session,
            type=ConversationType.DIRECT,
            title=f"与 {member.name} 的私聊",
            member_ids=[me.id, member.id],
        )
    return await _detail(session, conversation.id)


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: str, session: AsyncSession = Depends(get_session)
) -> ConversationOut:
    return await _detail(session, conversation_id)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    before_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[MessageOut]:
    conversation = await conv_repo.get_conversation(session, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = await message_repo.list_messages(
        session, conversation_id, limit=limit, before_id=before_id
    )
    return [message_out(message) for message in messages]


@router.post(
    "/conversations/{conversation_id}/members",
    response_model=MemberAdded,
)
async def add_member(
    conversation_id: str,
    payload: MemberRef,
    session: AsyncSession = Depends(get_session),
) -> MemberAdded:
    conversation = await conv_repo.get_conversation(session, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conversation.type is ConversationType.DIRECT:
        raise HTTPException(status_code=400, detail="私聊不能增加成员")

    member = await member_repo.get_member(session, payload.member_id)
    if member is None or not member.is_active:
        raise HTTPException(status_code=400, detail="成员不存在或已停用")

    added = await conv_repo.add_member(session, conversation_id, member.id)
    if not added:
        raise HTTPException(status_code=400, detail="该成员已在会话中")
    return MemberAdded(added=True)


@router.delete(
    "/conversations/{conversation_id}/members/{member_id}",
    response_model=MemberAdded,
)
async def remove_member(
    conversation_id: str,
    member_id: str,
    session: AsyncSession = Depends(get_session),
) -> MemberAdded:
    conversation = await conv_repo.get_conversation(session, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conversation.type is ConversationType.DIRECT:
        raise HTTPException(status_code=400, detail="私聊不能移除成员")

    removed = await conv_repo.remove_member(session, conversation_id, member_id)
    if not removed:
        raise HTTPException(status_code=404, detail="该成员不在会话中")
    return MemberAdded(added=False)
