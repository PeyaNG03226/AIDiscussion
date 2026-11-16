"""模型客户端契约。"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LLMMessage:
    role: str  # system / user / assistant
    content: str


class LLMClient(Protocol):
    """所有模型客户端都只需实现「流式对话」这一件事。"""

    def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...
