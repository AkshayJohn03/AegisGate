"""Retry with exponential backoff and FULL jitter.

Full jitter (sleep a uniform random duration in [0, backoff]) spreads retry
storms across clients far better than equal backoff. Retryability is
delegated to the exception: LLMError.retryable covers 429/5xx/timeout;
content 4xx errors are never retried.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    base_delay_s: float = 0.05
    max_delay_s: float = 1.0


def _default_retryable(exc: Exception) -> bool:
    return bool(getattr(exc, "retryable", False))


async def execute_with_retry(
    fn: Callable[[], Awaitable],
    policy: RetryPolicy,
    *,
    is_retryable: Callable[[Exception], bool] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> object:
    """Run ``fn`` with retries. Returns fn's result or raises the last error."""
    check = is_retryable or _default_retryable
    do_sleep = sleep or asyncio.sleep
    attempt = 0
    while True:
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 - re-raised below
            if attempt >= policy.max_retries or not check(exc):
                raise
            backoff = min(policy.max_delay_s, policy.base_delay_s * (2**attempt))
            delay = random.uniform(0, backoff)  # full jitter
            attempt += 1
            await do_sleep(delay)
