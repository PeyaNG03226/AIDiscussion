"""成员仓储测试。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aidiscussion.db.models import MemberKind
from aidiscussion.repositories import members as repo


async def test_create_and_list_members(session: AsyncSession) -> None:
    await repo.create_member(session, name="地产顾问", model="deepseek-chat")
    await repo.create_member(session, name="法务", avatar="law.png")

    all_members = await repo.list_members(session)
    ai_members = await repo.list_members(session, kind=MemberKind.AI)

    assert [member.name for member in all_members] == ["地产顾问", "法务"]
    assert len(ai_members) == 2
    assert ai_members[0].kind is MemberKind.AI


async def test_duplicate_name_rejected(session: AsyncSession) -> None:
    await repo.create_member(session, name="毒舌")

    with pytest.raises(ValueError, match="已存在"):
        await repo.create_member(session, name="毒舌")


async def test_deactivated_name_can_be_reused(session: AsyncSession) -> None:
    created = await repo.create_member(session, name="分析师", model="old-model")
    assert await repo.deactivate_member(session, created.id) is True

    revived = await repo.create_member(session, name="分析师", model="new-model")

    assert revived.id == created.id
    assert revived.is_active is True
    assert revived.model == "new-model"
    assert len(await repo.list_members(session)) == 1


async def test_get_self_member_is_idempotent(session: AsyncSession) -> None:
    first = await repo.get_self_member(session)
    second = await repo.get_self_member(session)

    assert first.id == second.id
    assert first.kind is MemberKind.HUMAN
    assert first.name == "我"


async def test_update_member_rejects_unknown_fields(session: AsyncSession) -> None:
    member = await repo.create_member(session, name="程序员")

    with pytest.raises(ValueError, match="不允许修改"):
        await repo.update_member(session, member.id, password="123456")


async def test_update_member_changes_persona(session: AsyncSession) -> None:
    member = await repo.create_member(session, name="老师")

    updated = await repo.update_member(
        session, member.id, system_prompt="你是耐心的老师", temperature=0.2
    )

    assert updated.temperature == 0.2
    assert updated.system_prompt == "你是耐心的老师"
