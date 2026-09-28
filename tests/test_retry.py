"""Retry engine: exponential backoff with full jitter, retryable-only."""

from __future__ import annotations

import asyncio

from aegisgate.llm.base import LLMError
from aegisgate.router.retry import RetryPolicy, execute_with_retry


def test_retries_retryable_errors_then_succeeds():
    attempts: list[int] = []
    sleeps: list[float] = []

    async def fn():
        attempts.append(1)
        if len(attempts) < 3:
            raise LLMError("rate limited", retryable=True, status_code=429)
        return "ok"

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    result = asyncio.run(
        execute_with_retry(
            fn,
            RetryPolicy(max_retries=3, base_delay_s=0.1, max_delay_s=1.0),
            sleep=fake_sleep,
        )
    )
    assert result == "ok"
    assert len(attempts) == 3
    assert len(sleeps) == 2
    # full jitter: delays are uniform in [0, backoff]; backoff doubles per attempt
    assert 0 <= sleeps[0] <= 0.1
    assert 0 <= sleeps[1] <= 0.2


def test_no_retry_on_content_4xx_errors():
    attempts: list[int] = []

    async def fn():
        attempts.append(1)
        raise LLMError("bad request", retryable=False, status_code=400)

    async def fake_sleep(seconds: float) -> None:
        raise AssertionError("must not sleep on non-retryable errors")

    try:
        asyncio.run(execute_with_retry(fn, RetryPolicy(max_retries=5), sleep=fake_sleep))
        raise AssertionError("expected LLMError")
    except LLMError as exc:
        assert exc.status_code == 400
    assert len(attempts) == 1


def test_gives_up_after_max_retries():
    attempts: list[int] = []

    async def fn():
        attempts.append(1)
        raise LLMError("still down", retryable=True, status_code=503)

    async def fake_sleep(seconds: float) -> None:
        return None

    try:
        asyncio.run(
            execute_with_retry(
                fn, RetryPolicy(max_retries=2, base_delay_s=0.01), sleep=fake_sleep
            )
        )
        raise AssertionError("expected LLMError")
    except LLMError:
        pass
    assert len(attempts) == 3  # 1 initial + 2 retries


def test_backoff_is_capped_at_max_delay():
    sleeps: list[float] = []

    async def fn():
        raise LLMError("down", retryable=True)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    try:
        asyncio.run(
            execute_with_retry(
                fn,
                RetryPolicy(max_retries=5, base_delay_s=0.5, max_delay_s=1.0),
                sleep=fake_sleep,
            )
        )
    except LLMError:
        pass
    assert all(s <= 1.0 for s in sleeps)  # jitter never exceeds the cap
    assert len(sleeps) == 5
