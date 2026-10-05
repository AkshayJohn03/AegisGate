"""LLM access boundary.

All model traffic in AegisGate goes through the :class:`LLMClient` protocol.
The gateway core never imports a concrete vendor SDK; it only knows this
interface plus the pydantic message/response contracts below. That is what
makes offline testing (``EchoMockClient``) and provider swaps trivial.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str = "user"
    content: str


class ToolSpec(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """A tool invocation produced by the model (OpenAI function-call shape,
    arguments already parsed from JSON)."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    id: str = ""


class ChatRequest(BaseModel):
    """Normalized chat request (OpenAI-compatible subset)."""

    model: str
    messages: list[Message]
    temperature: float = 0.3
    max_tokens: int | None = None
    tools: list[ToolSpec] | None = None
    stream: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ChatResponse(BaseModel):
    content: str
    model: str
    provider: str = ""
    finish_reason: str = "stop"
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float | None = None
    request_id: str | None = None
    tool_calls: list[ToolCall] | None = None  # model-requested tool invocations


class ChatChunk(BaseModel):
    """One streamed delta; the final chunk may carry full usage."""

    delta_content: str = ""
    model: str = ""
    finish_reason: str | None = None
    usage: Usage | None = None


class LLMError(Exception):
    """Error raised by any LLMClient implementation.

    ``retryable`` drives the retry/backoff engine: 429, 5xx, timeouts and
    transport errors are retryable; other 4xx (bad request, context length,
    content filter) are not — retrying them is pure waste.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        provider: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.provider = provider


class ModelNotAvailableError(LLMError):
    def __init__(self, model: str, reason: str = "unavailable") -> None:
        super().__init__(f"model {model!r} not available: {reason}", retryable=True)
        self.model = model


class LLMClient(Protocol):
    """The only seam between AegisGate and any LLM provider."""

    async def complete(self, request: ChatRequest) -> ChatResponse: ...

    def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]: ...
