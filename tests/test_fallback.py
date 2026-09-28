"""Fallback chain: ordered candidates, attempt budget, availability skips."""

from __future__ import annotations

import asyncio

import pytest

from aegisgate.llm.base import LLMError
from aegisgate.router.fallback import AllCandidatesFailedError, FallbackChain


def flaky_attempt(fail_for: set[str]):
    async def attempt(model: str):
        if model in fail_for:
            raise LLMError(f"{model} is down", retryable=True, status_code=503)
        return f"ok:{model}"

    return attempt


def test_falls_back_to_next_model_on_failure():
    chain = FallbackChain(max_attempts=3)
    outcome = asyncio.run(chain.run(["a", "b", "c"], flaky_attempt({"a"})))
    assert outcome.served_by == "b"
    assert outcome.response == "ok:b"
    statuses = {a.model: a.status for a in outcome.attempts}
    assert statuses == {"a": "error", "b": "ok"}


def test_skips_unavailable_models():
    chain = FallbackChain(max_attempts=3)
    outcome = asyncio.run(
        chain.run(
            ["a", "b", "c"],
            flaky_attempt(set()),
            availability_fn=lambda m: m not in {"a", "b"},
        )
    )
    assert outcome.served_by == "c"
    assert outcome.attempts[0].status == "skipped"
    assert outcome.attempts[0].model == "a"


def test_attempt_budget_exhaustion_raises():
    chain = FallbackChain(max_attempts=2)
    with pytest.raises(AllCandidatesFailedError) as excinfo:
        asyncio.run(chain.run(["a", "b", "c"], flaky_attempt({"a", "b", "c"})))
    assert len(excinfo.value.attempts) == 2


def test_hedge_used_for_first_pair():
    seen: list[str] = []

    async def attempt(model: str):
        seen.append(model)
        if model == "primary":
            await asyncio.sleep(0.2)
        return f"ok:{model}"

    from aegisgate.router.hedging import HedgingConfig

    chain = FallbackChain(max_attempts=3)
    outcome = asyncio.run(
        chain.run(
            ["primary", "shadow", "third"],
            attempt,
            hedge=True,
            hedge_config=HedgingConfig(enabled=True, hedge_delay_s=0.01, p95_threshold_ms=100),
            p95_fn=lambda m: 1000.0,
        )
    )
    assert outcome.served_by == "shadow"
    assert outcome.hedged is True
    assert outcome.response == "ok:shadow"
