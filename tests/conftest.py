"""测试夹具：每个用例使用独立的内存数据库。"""

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool

from aidiscussion.db.database import Database


@pytest_asyncio.fixture
async def db() -> AsyncIterator[Database]:
    database = Database("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    await database.create_all()
    yield database
    await database.dispose()


@pytest_asyncio.fixture
async def session(db: Database) -> AsyncIterator[AsyncSession]:
    async with db.session() as db_session:
        yield db_session
