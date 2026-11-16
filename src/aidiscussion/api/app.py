"""FastAPI 应用装配。"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from aidiscussion import __version__
from aidiscussion.api import rest, ws
from aidiscussion.db.database import Database, get_database
from aidiscussion.llm.base import LLMClient
from aidiscussion.llm.factory import get_llm
from aidiscussion.services.workspace import ensure_workspace

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(*, db: Database | None = None, llm: LLMClient | None = None) -> FastAPI:
    """允许注入数据库与模型客户端，方便测试时替换成临时库和假模型。"""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database: Database = application.state.db
        await database.create_all()
        async with database.session() as session:
            await ensure_workspace(session)

        if application.state.llm is None:
            application.state.llm = get_llm()

        yield

        if application.state.llm is not None:
            await application.state.llm.aclose()
        await database.dispose()

    app = FastAPI(title="AIDiscussion", version=__version__, lifespan=lifespan)
    app.state.db = db or get_database()
    app.state.llm = llm

    app.include_router(rest.router)
    app.include_router(ws.router)

    # 静态前端必须最后挂载："/" 会兜住所有未匹配路径，放在路由后面才不会遮蔽 /api 与 /ws
    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app
