"""OpenAI-compatible client: retries, error mapping, SSE parsing — all on
httpx.MockTransport (no network)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from aegisgate.llm.base import ChatRequest, LLMError, Message
from aegisgate.llm.openai_compat import OpenAICompatClient


def make_client(handler) -> tuple[OpenAICompatClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    client = OpenAICompatClient(http_client=http, api_key="secret", provider="test")
    return client, http


def request() -> ChatRequest:
    return ChatRequest(
        model="gpt-4o", messages=[Message(role="user", content="ping")], temperature=0.2
    )


def test_complete_parses_openai_shape_and_sends_auth():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.read()
        return httpx.Response(
            200,
            json={
                "id": "cmpl-1",
                "model": "gpt-4o",
                "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    client, http = make_client(handler)

    async def main():
        try:
            return await client.complete(request())
        finally:
            await http.aclose()

    response = asyncio.run(main())
    assert response.content == "pong"
    assert response.usage.total_tokens == 5
    assert seen["auth"] == "Bearer secret"
    payload = json.loads(seen["body"])
    assert payload["stream"] is False
    assert payload["model"] == "gpt-4o"


def test_retries_on_500_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="upstream boom")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "recovered"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    client, http = make_client(handler)

    async def main():
        try:
            return await client.complete(request())
        finally:
            await http.aclose()

    response = asyncio.run(main())
    assert response.content == "recovered"
    assert calls["n"] == 2


def test_4xx_content_error_is_not_retryable():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad request")

    client, http = make_client(handler)

    async def main():
        try:
            await client.complete(request())
        finally:
            await http.aclose()

    with pytest.raises(LLMError) as excinfo:
        asyncio.run(main())
    assert excinfo.value.retryable is False
    assert excinfo.value.status_code == 400
    assert calls["n"] == 1


def test_429_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    client, http = make_client(handler)

    async def main():
        try:
            await client.complete(request())
        finally:
            await http.aclose()

    with pytest.raises(LLMError) as excinfo:
        asyncio.run(main())
    assert excinfo.value.retryable is True


def test_sse_streaming_parses_chunks_and_done():
    sse_body = (
        'data: {"model":"gpt-4o","choices":[{"delta":{"content":"Hel"}}]}\n\n'
        "\n"
        'data: {"model":"gpt-4o","choices":[{"delta":{"content":"lo"},"finish_reason":null}]}\n\n'
        'data: {"model":"gpt-4o","choices":[{"delta":{},"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":2,"completion_tokens":2}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["stream"] is True
        return httpx.Response(
            200, text=sse_body, headers={"content-type": "text/event-stream"}
        )

    client, http = make_client(handler)

    async def main():
        chunks = []
        try:
            async for chunk in client.stream(request()):
                chunks.append(chunk)
        finally:
            await http.aclose()
        return chunks

    chunks = asyncio.run(main())
    deltas = [c.delta_content for c in chunks]
    assert deltas[:2] == ["Hel", "lo"]
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage is not None and chunks[-1].usage.total_tokens == 4


def test_stream_start_error_surfaces_immediately():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    client, http = make_client(handler)

    async def main():
        try:
            async for _ in client.stream(request()):
                pass
        finally:
            await http.aclose()

    with pytest.raises(LLMError) as excinfo:
        asyncio.run(main())
    assert excinfo.value.status_code == 401
