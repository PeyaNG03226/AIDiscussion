"""数据库连接与事务管理。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aidiscussion.config import Settings, get_settings
from aidiscussion.db.base import Base


class Database:
    """持有一个异步引擎和会话工厂。

    仓储函数只 flush 不 commit，事务边界统一由 Database.session() 控制，
    这样「建会话 + 加成员 + 写消息」这类组合操作可以保证原子性。
    """

    def __init__(self, url: str, **engine_kwargs: Any) -> None:
        self.engine = create_async_engine(url, **engine_kwargs)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


_database: Database | None = None


def get_database(settings: Settings | None = None) -> Database:
    """进程内单例，供命令行与后续 Web 服务共用。"""
    global _database
    if _database is None:
        current = settings or get_settings()
        current.ensure_data_dir()
        _database = Database(current.resolved_database_url)
    return _database
