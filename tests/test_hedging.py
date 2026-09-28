"""Hedging: first response wins, loser cancelled; gated on delay + p95."""

from __future__ import annotations

import asyncio

from aegisgate.router.hedging import HedgingConfig, execute_with_hedging


def test_hedge_fires_and_shadow_wins_with_loser_cancelled():
    cancelled = asyncio.Event()

    async def slow_primary():
        try:
            await asyncio.sleep(0.5)
            return "slow"
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def fast_shadow():
        await asyncio.sleep(0.01)
        return "fast"

    async def main():
        config = HedgingConfig(enabled=True, hedge_delay_s=0.02, p95_threshold_ms=1000)
        result = await execute_with_hedging(
            slow_primary, fast_shadow, config=config, p95_latency_ms=2000
        )
        assert result.value == "fast"
        assert result.winner == "shadow"
        assert result.hedged is True
        await asyncio.sleep(0.05)  # let cancellation propagate
        assert cancelled.is_set(), "loser must be cancelled"

    asyncio.run(main())


def test_primary_wins_race_when_it_finishes_first():
    async def quick_primary():
        return "quick"

    async def slow_shadow():
        await asyncio.sleep(0.5)
        return "slow"

    async def main():
        config = HedgingConfig(enabled=True, hedge_delay_s=0.02, p95_threshold_ms=1000)
        # Primary answers within the hedge delay -> no hedge at all.
        result = await execute_with_hedging(
            quick_primary, slow_shadow, config=config, p95_latency_ms=2000
        )
        assert result.value == "quick"
        assert result.winner == "primary"
        assert result.hedged is False

    asyncio.run(main())


def test_no_hedge_when_p95_below_threshold():
    shadow_called = asyncio.Event()

    async def slow_primary():
        await asyncio.sleep(0.03)
        return "primary"

    async def shadow():
        shadow_called.set()
        return "shadow"

    async def main():
        config = HedgingConfig(enabled=True, hedge_delay_s=0.01, p95_threshold_ms=1000)
        result = await execute_with_hedging(
            slow_primary, shadow, config=config, p95_latency_ms=100.0
        )
        assert result.value == "primary"
        assert result.hedged is False
        assert not shadow_called.is_set()

    asyncio.run(main())


def test_hedge_survives_shadow_failure():
    async def slow_primary():
        await asyncio.sleep(0.06)
        return "primary"

    async def broken_shadow():
        await asyncio.sleep(0.01)
        raise RuntimeError("shadow exploded")

    async def main():
        config = HedgingConfig(enabled=True, hedge_delay_s=0.01, p95_threshold_ms=1000)
        result = await execute_with_hedging(
            slow_primary, broken_shadow, config=config, p95_latency_ms=2000
        )
        assert result.value == "primary"
        assert result.winner == "primary"
        assert result.hedged is True

    asyncio.run(main())


def test_both_failing_raises():
    async def broken():
        raise RuntimeError("down")

    async def main():
        config = HedgingConfig(enabled=True, hedge_delay_s=0.01, p95_threshold_ms=1000)
        try:
            await execute_with_hedging(broken, broken, config=config, p95_latency_ms=2000)
            raise AssertionError("expected RuntimeError")
        except RuntimeError:
            pass

    asyncio.run(main())
