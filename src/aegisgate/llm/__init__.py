"""LLM client seam: protocol + offline and OpenAI-compatible implementations."""

from aegisgate.llm.base import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    LLMClient,
    LLMError,
    Message,
    ToolSpec,
    Usage,
)
from aegisgate.llm.echo import EchoMockClient
from aegisgate.llm.openai_compat import OpenAICompatClient

__all__ = [
    "ChatChunk",
    "ChatRequest",
    "ChatResponse",
    "EchoMockClient",
    "LLMClient",
    "LLMError",
    "Message",
    "OpenAICompatClient",
    "ToolSpec",
    "Usage",
]
