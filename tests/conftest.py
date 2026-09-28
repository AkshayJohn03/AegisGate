"""Shared fixtures: deterministic clock, registry, request factory."""

from __future__ import annotations

import pytest

from aegisgate.clock import ManualClock
from aegisgate.llm.base import ChatRequest, Message
from aegisgate.router.registry import ModelRegistry


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry.load()


@pytest.fixture
def make_request():
    def _make(
        content: str = "hello",
        model: str = "gpt-4o",
        temperature: float = 0.3,
        **kwargs,
    ) -> ChatRequest:
        return ChatRequest(
            model=model,
            messages=[Message(role="user", content=content)],
            temperature=temperature,
            **kwargs,
        )

    return _make
