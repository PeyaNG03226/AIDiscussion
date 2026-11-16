"""离线 Mock 模型：未配置密钥时用来跑通整条链路。"""

import asyncio
from collections.abc import AsyncIterator, Sequence

from aidiscussion.llm.base import LLMMessage


class MockLLM:
    def __init__(self, *, chunk_size: int = 6, delay: float = 0.0) -> None:
        self._chunk_size = max(1, chunk_size)
        self._delay = delay

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        last_user = next(
            (message for message in reversed(list(messages)) if message.role == "user"), None
        )
        reply = (
            f"（离线 Mock）收到你的消息：{last_user.content}"
            if last_user is not None
            else "（离线 Mock）你好，配置密钥后即可接入真实模型。"
        )

        for start in range(0, len(reply), self._chunk_size):
            if self._delay > 0:
                await asyncio.sleep(self._delay)
            yield reply[start : start + self._chunk_size]

    async def aclose(self) -> None:
        return None
