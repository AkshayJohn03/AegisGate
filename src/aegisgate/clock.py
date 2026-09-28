"""Clock abstraction.

Every time-dependent component (circuit breakers, token buckets, cache TTL,
budget windows) takes a ``Clock`` instead of calling ``time.time()`` directly.
This makes the whole control plane testable with a ``ManualClock`` — no
sleeping, no flakes, fully offline.
"""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    """Minimal time source: seconds since an arbitrary epoch."""

    def now(self) -> float: ...


class SystemClock:
    """Wall-clock time source used in production."""

    def now(self) -> float:
        return time.time()


class ManualClock:
    """Deterministic clock for tests: advance it explicitly."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += float(seconds)
