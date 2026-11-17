"""模型接入层测试：全部走 httpx.MockTransport，不产生真实网络请求。"""

import json
from collections.abc import Callable

import httpx
import pytest

from aidiscussion.config import Settings
from aidiscussion.llm.base import LLMClient, LLMMessage
from aidiscussion.llm.errors import (
    LLMAuthError,
    LLMConnectionError,
    LLMResponseError,
    LLMServerError,
)
from aidiscussion.llm.factory import build_llm_for
from aidiscussion.llm.mock import MockLLM
from aidiscussion.llm.openai_compatible import OpenAICompatibleClient, parse_sse_line

SSE_BODY = (
    'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"，世界"}}]}\n\n'
    "data: [DONE]\n\n"
)


def make_client(
    handler: Callable[[httpx.Request], httpx.Response], *, max_retries: int = 2
) -> OpenAICompatibleClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://llm.test/v1")
    return OpenAICompatibleClient(
        api_key="sk-test",
        base_url="https://llm.test/v1",
        model="test-model",
        max_retries=max_retries,
        backoff=0,
        client=http,
    )


async def collect(client: LLMClient, *texts: str) -> str:
    messages = [LLMMessage(role="user", content=text) for text in texts]
    return "".join([piece async for piece in client.stream_chat(messages)])


async def test_stream_parses_sse_and_sends_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, text=SSE_BODY)

    client = make_client(handler)
    text = await collect(client, "在吗")
    await client.aclose()

    assert text == "你好，世界"
    assert str(captured["url"]).endswith("/v1/chat/completions")
    assert captured["auth"] == "Bearer sk-test"

    body = captured["body"]
    assert body["model"] == "test-model"  # type: ignore[index]
    assert body["stream"] is True  # type: ignore[index]
    assert body["temperature"] == 0.7  # type: ignore[index]
    assert body["messages"] == [{"role": "user", "content": "在吗"}]  # type: ignore[index]


async def test_client_can_omit_temperature_for_strict_models() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, text=SSE_BODY)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://llm.test/v1")
    client = OpenAICompatibleClient(
        api_key="sk-test",
        base_url="https://llm.test/v1",
        model="kimi-test",
        max_retries=0,
        backoff=0,
        send_temperature=False,
        client=http,
    )

    await collect(client, "在吗")
    await client.aclose()

    assert "temperature" not in captured["body"]  # type: ignore[operator]


async def test_auth_error_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "invalid key"})

    client = make_client(handler)

    with pytest.raises(LLMAuthError):
        await collect(client, "你好")
    assert calls == 1


async def test_rate_limit_is_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, text=SSE_BODY)

    client = make_client(handler, max_retries=1)

    assert await collect(client, "你好") == "你好，世界"
    assert calls == 2


async def test_server_error_raises_after_retries_exhausted() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="overloaded")

    client = make_client(handler, max_retries=1)

    with pytest.raises(LLMServerError):
        await collect(client, "你好")
    assert calls == 2


async def test_connection_error_is_wrapped() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(handler, max_retries=1)

    with pytest.raises(LLMConnectionError):
        await collect(client, "你好")
    assert calls == 2


async def test_interrupted_stream_is_not_retried() -> None:
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield 'data: {"choices":[{"delta":{"content":"半"}}]}\n\n'.encode()
            raise httpx.ReadError("connection lost")

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=BrokenStream())

    client = make_client(handler, max_retries=2)
    pieces: list[str] = []

    with pytest.raises(LLMConnectionError):
        async for piece in client.stream_chat([LLMMessage(role="user", content="你好")]):
            pieces.append(piece)

    assert pieces == ["半"]
    assert calls == 1


async def test_malformed_sse_payload_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="data: not-json\n\n")

    client = make_client(handler)

    with pytest.raises(LLMResponseError):
        await collect(client, "你好")


def test_parse_sse_line() -> None:
    assert parse_sse_line('data: {"a":1}') == '{"a":1}'
    assert parse_sse_line(": keep-alive") is None
    assert parse_sse_line("") is None
    assert parse_sse_line("event: message") is None


async def test_mock_streams_echo_reply() -> None:
    llm = MockLLM(chunk_size=3)

    text = await collect(llm, "测试消息")

    assert "测试消息" in text


async def test_factory_falls_back_to_mock_without_key() -> None:
    settings = Settings(_env_file=None)
    llm = build_llm_for(settings.provider_spec(None), settings)
    try:
        assert isinstance(llm, MockLLM)
    finally:
        await llm.aclose()


async def test_factory_builds_openai_client_with_key() -> None:
    settings = Settings(_env_file=None, llm_api_key="sk-12345678")
    llm = build_llm_for(settings.provider_spec("deepseek"), settings)
    try:
        assert isinstance(llm, OpenAICompatibleClient)
    finally:
        await llm.aclose()


def test_each_provider_uses_its_own_endpoint_and_key() -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="sk-deepseek",
        doubao_api_key="sk-doubao",
        kimi_api_key="sk-kimi",
        qwen_api_key="sk-qwen",
    )

    doubao = settings.provider_spec("doubao")
    kimi = settings.provider_spec("kimi")
    qwen = settings.provider_spec("qwen")
    deepseek = settings.provider_spec(None)

    assert doubao.base_url.startswith("https://ark.cn-beijing.volces.com")
    assert kimi.base_url == "https://api.moonshot.cn/v1"
    assert qwen.base_url.startswith("https://dashscope.aliyuncs.com")
    # 未填 deepseek_api_key 时沿用旧的 llm_* 配置
    assert deepseek.api_key is not None
    assert deepseek.api_key.get_secret_value() == "sk-deepseek"
    assert deepseek.has_key and doubao.has_key and kimi.has_key and qwen.has_key
    # Kimi 的 k 系列只接受 temperature=1，别给它发 temperature
    assert kimi.send_temperature is False
    assert doubao.send_temperature is True


def test_unknown_provider_falls_back_to_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.provider_spec("openai").id == "deepseek"
    assert settings.provider_spec(None).id == "deepseek"
