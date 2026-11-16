"""WebSocket：消息收发与流式回复推送。

协议（JSON 文本帧）：
客户端 -> {"type": "send", "content": "...", "mode": "mention|free|supervisor"}
服务端 -> user_message / reply_start / reply_delta / reply_done / reply_failed / error
"""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from aidiscussion.api.schemas import member_out, message_out
from aidiscussion.db.database import Database
from aidiscussion.llm.base import LLMClient
from aidiscussion.repositories import conversations as conv_repo
from aidiscussion.repositories import members as member_repo
from aidiscussion.services import chat
from aidiscussion.services.chat import ReplyDelta, ReplyDone, ReplyFailed, ReplyStart

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_MODES = {chat.ReplyMode.MENTION, chat.ReplyMode.FREE, chat.ReplyMode.SUPERVISOR}


@router.websocket("/ws/conversations/{conversation_id}")
async def chat_socket(websocket: WebSocket, conversation_id: str) -> None:
    await websocket.accept()
    db: Database = websocket.app.state.db
    llm: LLMClient = websocket.app.state.llm

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await _error(websocket, "消息不是合法 JSON")
                continue

            if payload.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            content = str(payload.get("content") or "").strip()
            if not content:
                await _error(websocket, "消息内容不能为空")
                continue

            mode = payload.get("mode") or chat.ReplyMode.MENTION
            if mode not in VALID_MODES:
                mode = chat.ReplyMode.MENTION

            await _handle_send(websocket, db, llm, conversation_id, content, mode)
    except WebSocketDisconnect:
        return


async def _error(websocket: WebSocket, message: str) -> None:
    await websocket.send_json({"type": "error", "message": message})


async def _handle_send(
    websocket: WebSocket,
    db: Database,
    llm: LLMClient,
    conversation_id: str,
    content: str,
    mode: str,
) -> None:
    # 第一段事务只负责保存用户消息，先落库再开始长时间流式输出
    async with db.session() as session:
        conversation = await conv_repo.get_conversation(session, conversation_id)
        if conversation is None:
            await _error(websocket, "会话不存在")
            return
        me = await member_repo.get_self_member(session)
        user_message = await chat.post_user_message(
            session,
            conversation_id=conversation_id,
            sender_id=me.id,
            content=content,
        )
        user_payload = message_out(user_message, sender_name=me.name)

    await websocket.send_json(
        {"type": "user_message", "message": user_payload.model_dump(mode="json")}
    )

    async with db.session() as session:
        conversation = await conv_repo.get_conversation(session, conversation_id)
        if conversation is None:
            await _error(websocket, "会话不存在")
            return

        async for event in chat.generate_replies(
            session,
            llm,
            conversation=conversation,
            user_message=user_message,
            mode=mode,
        ):
            if isinstance(event, ReplyStart):
                await websocket.send_json(
                    {
                        "type": "reply_start",
                        "member": member_out(event.member).model_dump(mode="json"),
                    }
                )
            elif isinstance(event, ReplyDelta):
                await websocket.send_json(
                    {"type": "reply_delta", "member_id": event.member_id, "text": event.text}
                )
            elif isinstance(event, ReplyDone):
                await websocket.send_json(
                    {
                        "type": "reply_done",
                        "message": message_out(
                            event.message, sender_name=event.member.name
                        ).model_dump(mode="json"),
                    }
                )
            elif isinstance(event, ReplyFailed):
                await websocket.send_json(
                    {
                        "type": "reply_failed",
                        "member_id": event.member_id,
                        "error": event.error,
                    }
                )
