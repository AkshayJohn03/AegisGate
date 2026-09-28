"""Request hedging: race a shadow request against a slow primary.

If the primary hasn't answered within ``hedge_delay_s`` *and* its observed
p95 latency exceeds a threshold, we fire a duplicate at a shadow provider.
First successful response wins; the loser task is cancelled. Hedging only
pays off for tail latency on idempotent requests, which is why it is gated
on both the delay and the p95 signal.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass


@dataclass(frozen=True)
class HedgingConfig:
    enabled: bool = True
    hedge_delay_s: float = 0.25
    p95_threshold_ms: float = 1500.0


@dataclass
class HedgeResult:
    value: object
    winner: str  # "primary" | "shadow"
    hedged: bool


def _result(task: asyncio.Task) -> object:
    exc = task.exception()
    if exc is not None:
        raise exc
    return task.result()


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task


async def execute_with_hedging(
    primary_fn: Callable[[], Awaitable],
    shadow_fn: Callable[[], Awaitable] | None,
    *,
    config: HedgingConfig,
    p95_latency_ms: float,
) -> HedgeResult:
    primary_task = asyncio.create_task(primary_fn())
    done, _ = await asyncio.wait({primary_task}, timeout=config.hedge_delay_s)
    if primary_task in done:
        return HedgeResult(value=_result(primary_task), winner="primary", hedged=False)

    if not config.enabled or shadow_fn is None or p95_latency_ms <= config.p95_threshold_ms:
        # Not worth hedging; ride the primary out.
        return HedgeResult(value=await primary_task, winner="primary", hedged=False)

    shadow_task = asyncio.create_task(shadow_fn())
    await asyncio.wait({primary_task, shadow_task}, return_when=asyncio.FIRST_COMPLETED)

    if primary_task.done() and primary_task.exception() is None:
        await _cancel(shadow_task)
        return HedgeResult(value=primary_task.result(), winner="primary", hedged=True)
    if shadow_task.done() and shadow_task.exception() is None:
        await _cancel(primary_task)
        return HedgeResult(value=shadow_task.result(), winner="shadow", hedged=True)

    # Exactly one task finished with an error (or both did): ride the survivor.
    if primary_task.done():  # primary failed -> the shadow is still running
        return HedgeResult(value=await shadow_task, winner="shadow", hedged=True)
    await _cancel(shadow_task)  # shadow failed -> the primary is still running
    return HedgeResult(value=await primary_task, winner="primary", hedged=True)
