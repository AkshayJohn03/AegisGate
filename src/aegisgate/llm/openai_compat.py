"""OpenAI-compatible client on httpx with SSE streaming and retries.

Works against any server that speaks ``POST /v1/chat/completions`` (OpenAI,
vLLM, Together, Groq, Ollama's OpenAI shim, ...). 429/5xx/timeouts are marked
retryable and handed to the shared backoff engine; other 4xx fail fast.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from aegisgate.llm.base import ChatChunk, ChatRequest, ChatResponse, LLMError, Usage
from aegisgate.router.retry import RetryPolicy, execute_with_retry

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class OpenAICompatClient:
    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        timeout_s: float = 30.0,
        retry_policy: RetryPolicy | None = None,
        http_client: httpx.AsyncClient | None = None,
        provider: str = "openai",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.provider = provider
        self.api_key = api_key
        self.retry_policy = retry_policy or RetryPolicy()
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=self.base_url, timeout=httpx.Timeout(timeout_s)
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _payload(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "stream": stream,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            payload["tools"] = [
                {"type": "function", "function": {"name": t.name, "parameters": t.parameters}}
                for t in request.tools
            ]
        return payload

    def _translate(self, exc: Exception) -> LLMError:
        if isinstance(exc, httpx.TimeoutException):
            return LLMError(f"timeout: {exc}", retryable=True, provider=self.provider)
        if isinstance(exc, httpx.TransportError):
            return LLMError(f"transport error: {exc}", retryable=True, provider=self.provider)
        if isinstance(exc, LLMError):
            return exc
        return LLMError(f"unexpected error: {exc}", retryable=False, provider=self.provider)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        payload = self._payload(request, stream=False)

        async def call() -> ChatResponse:
            try:
                resp = await self._client.post(
                    "/chat/completions", json=payload, headers=self._headers()
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise self._translate(exc) from exc
            if resp.status_code >= 400:
                raise LLMError(
                    f"upstream {resp.status_code}: {resp.text[:200]}",
                    retryable=resp.status_code in RETRYABLE_STATUS,
                    status_code=resp.status_code,
                    provider=self.provider,
                )
            return self._parse(resp.json())

        try:
            return await execute_with_retry(call, self.retry_policy)
        except LLMError:
            raise
        except Exception as exc:  # normalize anything odd into LLMError
            raise self._translate(exc) from exc

    def _parse(self, data: dict[str, Any]) -> ChatResponse:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage_raw = data.get("usage") or {}
        return ChatResponse(
            content=message.get("content") or "",
            model=data.get("model", ""),
            provider=self.provider,
            finish_reason=choice.get("finish_reason") or "stop",
            usage=Usage(
                prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                completion_tokens=int(usage_raw.get("completion_tokens") or 0),
            ),
            request_id=data.get("id"),
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        """SSE streaming. Stream start failures surface immediately (no retry:
        by the time chunks flow, retrying would duplicate partial output)."""
        payload = self._payload(request, stream=True)
        try:
            async with self._client.stream(
                "POST", "/chat/completions", json=payload, headers=self._headers()
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise LLMError(
                        f"upstream {resp.status_code}: {body[:200]}",
                        retryable=resp.status_code in RETRYABLE_STATUS,
                        status_code=resp.status_code,
                        provider=self.provider,
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if not data or data == "[DONE]":
                        if data == "[DONE]":
                            break
                        continue
                    obj = json.loads(data)
                    choice = (obj.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    usage_raw = obj.get("usage")
                    yield ChatChunk(
                        delta_content=delta.get("content") or "",
                        model=obj.get("model", request.model),
                        finish_reason=choice.get("finish_reason"),
                        usage=Usage(**usage_raw) if usage_raw else None,
                    )
        except LLMError:
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise self._translate(exc) from exc
