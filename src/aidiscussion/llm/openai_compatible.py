"""OpenAI 兼容接口客户端（DeepSeek / 通义 / OpenAI / Ollama 通用）。

直接用 httpx 解析 SSE，不引入厂商 SDK：少一层黑盒，方便统一做重试与错误映射。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from aidiscussion.llm.base import LLMMessage
from aidiscussion.llm.errors import (
    LLMAuthError,
    LLMConnectionError,
    LLMError,
    LLMRateLimitError,
    LLMRequestError,
    LLMResponseError,
    LLMServerError,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        max_retries: int = 2,
        backoff: float = 0.5,
        send_temperature: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._max_retries = max_retries
        self._backoff = backoff
        # 部分推理模型（如 Kimi 的 k 系列）只接受 temperature=1，这里干脆不传
        self._send_temperature = send_temperature
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=self._headers,
        )

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": model or self._model,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
            "stream": True,
        }
        if self._send_temperature:
            payload["temperature"] = temperature

        attempt = 0
        started = False
        while True:
            try:
                async for piece in self._stream_once(payload):
                    started = True
                    yield piece
                return
            except LLMError as exc:
                # 已经吐出内容再重试会造成重复文字，因此只对「还没开始输出」的失败重试
                if started or not exc.retryable or attempt >= self._max_retries:
                    raise
                attempt += 1
                delay = self._backoff * (2 ** (attempt - 1))
                logger.warning("模型请求失败（%s），%.1fs 后重试第 %d 次", exc, delay, attempt)
                if delay > 0:
                    await asyncio.sleep(delay)

    async def _stream_once(self, payload: dict[str, Any]) -> AsyncIterator[str]:
        try:
            async with self._client.stream(
                "POST", "/chat/completions", json=payload, headers=self._headers
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise error_from_response(response.status_code, body)

                async for line in response.aiter_lines():
                    raw = parse_sse_line(line)
                    if raw is None:
                        continue
                    if raw == "[DONE]":
                        return
                    content = extract_delta_content(raw)
                    if content:
                        yield content
        except httpx.HTTPError as exc:
            raise LLMConnectionError(f"连接模型服务失败：{exc}") from exc

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def parse_sse_line(line: str) -> str | None:
    """从 SSE 行中取出 data 字段；注释行、空行和事件名返回 None。"""
    line = line.strip()
    if not line or line.startswith(":") or not line.startswith("data:"):
        return None
    return line[len("data:") :].strip()


def extract_delta_content(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"无法解析模型返回数据：{raw[:80]}") from exc

    choices = payload.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    return content if isinstance(content, str) else ""


def error_from_response(status: int, body: str) -> LLMError:
    detail = body.strip()[:200] or "无响应内容"
    if status in (401, 403):
        return LLMAuthError(f"模型鉴权失败（{status}）：{detail}", status_code=status)
    if status == 429:
        return LLMRateLimitError(f"模型限流（429）：{detail}", status_code=status)
    if status >= 500:
        return LLMServerError(f"模型服务异常（{status}）：{detail}", status_code=status)
    return LLMRequestError(f"模型请求被拒绝（{status}）：{detail}", status_code=status)
