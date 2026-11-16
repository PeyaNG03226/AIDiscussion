"""REST 与 WebSocket 接口测试。"""

from collections.abc import AsyncIterator, Sequence

import httpx
import pytest_asyncio
from fastapi.testclient import TestClient

from aidiscussion.api.app import create_app
from aidiscussion.db.database import Database
from aidiscussion.llm.base import LLMMessage
from aidiscussion.services.workspace import ensure_workspace


class FakeLLM:
    def __init__(self, reply: str = "收到") -> None:
        self.reply = reply
        self.calls: list[list[LLMMessage]] = []

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        self.calls.append(list(messages))
        for char in self.reply:
            yield char

    async def aclose(self) -> None:
        return None


@pytest_asyncio.fixture
async def api(db: Database) -> AsyncIterator[tuple[httpx.AsyncClient, str, FakeLLM]]:
    llm = FakeLLM()
    app = create_app(db=db, llm=llm)
    async with db.session() as session:
        conversation = await ensure_workspace(session)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, conversation.id, llm


async def test_health_and_me(api) -> None:
    client, _, _ = api

    assert (await client.get("/api/health")).json() == {"status": "ok"}
    me = (await client.get("/api/me")).json()
    assert me["name"] == "我"
    assert me["kind"] == "human"


async def test_create_member_and_reject_duplicate(api) -> None:
    client, _, _ = api

    created = await client.post(
        "/api/members",
        json={"name": "财务", "system_prompt": "你是严谨的财务", "temperature": 0.3},
    )
    assert created.status_code == 201
    assert created.json()["name"] == "财务"

    duplicate = await client.post("/api/members", json={"name": "财务"})
    assert duplicate.status_code == 400

    members = (await client.get("/api/members")).json()
    assert "财务" in {member["name"] for member in members}


async def test_create_group_and_add_remove_member(api) -> None:
    client, _, _ = api
    member = (
        await client.post("/api/members", json={"name": "法律顾问"})
    ).json()

    group = await client.post(
        "/api/conversations",
        json={"title": "投资讨论", "member_ids": [member["id"]]},
    )
    assert group.status_code == 201
    conversation = group.json()
    assert conversation["title"] == "投资讨论"
    assert {item["name"] for item in conversation["members"]} == {"我", "法律顾问"}

    extra = (await client.post("/api/members", json={"name": "税务顾问"})).json()
    added = await client.post(
        f"/api/conversations/{conversation['id']}/members",
        json={"member_id": extra["id"]},
    )
    assert added.status_code == 200

    removed = await client.delete(
        f"/api/conversations/{conversation['id']}/members/{extra['id']}"
    )
    assert removed.status_code == 200

    detail = (await client.get(f"/api/conversations/{conversation['id']}")).json()
    assert {item["name"] for item in detail["members"]} == {"我", "法律顾问"}


async def test_direct_conversation_is_reused(api) -> None:
    client, _, _ = api
    members = (await client.get("/api/members")).json()
    assistant = next(member for member in members if member["kind"] == "ai")

    first = (
        await client.post("/api/conversations/direct", json={"member_id": assistant["id"]})
    ).json()
    second = (
        await client.post("/api/conversations/direct", json={"member_id": assistant["id"]})
    ).json()

    assert first["id"] == second["id"]
    assert first["type"] == "direct"


async def test_unknown_conversation_returns_404(api) -> None:
    client, _, _ = api

    response = await client.get("/api/conversations/not-exist")

    assert response.status_code == 404


async def test_messages_endpoint(api) -> None:
    client, conversation_id, _ = api

    response = await client.get(f"/api/conversations/{conversation_id}/messages?limit=10")

    assert response.status_code == 200
    assert response.json() == []


def test_websocket_streams_reply_and_persists(tmp_path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'ws.db'}")
    llm = FakeLLM("你好呀")
    app = create_app(db=db, llm=llm)

    with TestClient(app) as client:
        conversations = client.get("/api/conversations").json()
        conversation_id = conversations[0]["id"]

        with client.websocket_connect(f"/ws/conversations/{conversation_id}") as socket:
            socket.send_json(
                {"type": "send", "content": "@通用助手 在吗", "mode": "mention"}
            )
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event["type"] == "reply_done":
                    break

        assert events[0]["type"] == "user_message"
        reply = "".join(
            event.get("text", "") for event in events if event["type"] == "reply_delta"
        )
        assert reply == "你好呀"

        messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
        assert [item["sender_name"] for item in messages] == ["我", "通用助手"]


async def test_web_ui_is_served(api) -> None:
    client, _, _ = api

    page = await client.get("/")
    assert page.status_code == 200
    assert "AIDiscussion" in page.text

    script = await client.get("/app.js")
    assert script.status_code == 200
    assert "reply_delta" in script.text

    styles = await client.get("/styles.css")
    assert styles.status_code == 200
    assert "--accent" in styles.text
