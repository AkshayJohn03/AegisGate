"""Deterministic offline LLM client.

Echoes the last user message back with a stable prefix and derives token
counts from text length. No network, no keys, no randomness — the same input
always produces the same output, which is exactly what a test suite and an
offline demo need.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from aegisgate.llm.base import ChatChunk, ChatRequest, ChatResponse, Usage


def approx_tokens(text: str) -> int:
    """~4 chars per token heuristic; good enough for offline metering."""
    return max(1, round(len(text) / 4))


class EchoMockClient:
    def __init__(self, latency_ms: float = 12.0, prefix: str = "echo") -> None:
        self.latency_ms = latency_ms
        self.prefix = prefix
        self.calls: list[ChatRequest] = []

    @staticmethod
    def _user_text(request: ChatRequest) -> str:
        for message in reversed(request.messages):
            if message.role == "user":
                return message.content
        return request.messages[-1].content if request.messages else ""

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        user_text = self._user_text(request)
        content = f"[{self.prefix}:{request.model}] {user_text}"
        usage = Usage(
            prompt_tokens=approx_tokens("".join(m.content for m in request.messages)),
            completion_tokens=approx_tokens(content),
        )
        return ChatResponse(
            content=content,
            model=request.model,
            provider="echo",
            finish_reason="stop",
            usage=usage,
            latency_ms=self.latency_ms,
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        self.calls.append(request)
        user_text = self._user_text(request)
        content = f"[{self.prefix}:{request.model}] {user_text}"
        words = content.split(" ")
        for i, word in enumerate(words):
            piece = word if i == len(words) - 1 else f"{word} "
            yield ChatChunk(delta_content=piece, model=request.model)
        usage = Usage(
            prompt_tokens=approx_tokens("".join(m.content for m in request.messages)),
            completion_tokens=approx_tokens(content),
        )
        yield ChatChunk(delta_content="", model=request.model, finish_reason="stop", usage=usage)
