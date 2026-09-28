"""Circuit breaker per provider: closed / open / half-open.

The breaker keys on *provider*, not model: an outage is usually a provider
property (auth, quota, region), and one shared state prevents hammering a
dead provider with each of its models in turn. Time comes from an injected
Clock so tests never sleep.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from aegisgate.clock import Clock


class BreakerState(StrEnum):
    closed = "closed"
    open = "open"
    half_open = "half_open"


class CircuitBreakerConfig(BaseModel):
    failure_threshold: int = 5
    recovery_timeout_s: float = 30.0
    half_open_max_probes: int = 2


class CircuitBreaker:
    def __init__(self, name: str, config: CircuitBreakerConfig, clock: Clock) -> None:
        self.name = name
        self.config = config
        self.clock = clock
        self._state = BreakerState.closed
        self._failures = 0
        self._probes_used = 0
        self._opened_at = 0.0

    @property
    def state(self) -> BreakerState:
        return self._state

    def allow_request(self) -> bool:
        now = self.clock.now()
        if self._state is BreakerState.closed:
            return True
        if self._state is BreakerState.open:
            if now - self._opened_at >= self.config.recovery_timeout_s:
                self._state = BreakerState.half_open
                self._probes_used = 0
            else:
                return False
        # half_open: admit a limited number of probes
        if self._probes_used < self.config.half_open_max_probes:
            self._probes_used += 1
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._probes_used = 0
        self._state = BreakerState.closed

    def record_failure(self) -> None:
        if self._state is BreakerState.half_open:
            self.trip()
            return
        self._failures += 1
        if self._state is BreakerState.closed and self._failures >= self.config.failure_threshold:
            self.trip()

    def trip(self) -> None:
        """Force open (used by the health monitor on quarantine)."""
        self._state = BreakerState.open
        self._opened_at = self.clock.now()
        self._probes_used = 0

    def snapshot(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "state": self._state.value,
            "failures": self._failures,
            "probes_used": self._probes_used,
            "opened_at": self._opened_at,
        }


class BreakerRegistry:
    """One breaker per provider, created lazily."""

    def __init__(self, config: CircuitBreakerConfig, clock: Clock) -> None:
        self.config = config
        self.clock = clock
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, provider: str) -> CircuitBreaker:
        if provider not in self._breakers:
            self._breakers[provider] = CircuitBreaker(provider, self.config, self.clock)
        return self._breakers[provider]

    def allow(self, provider: str) -> bool:
        return self.get(provider).allow_request()

    def record_success(self, provider: str) -> None:
        self.get(provider).record_success()

    def record_failure(self, provider: str) -> None:
        self.get(provider).record_failure()

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {name: b.snapshot() for name, b in self._breakers.items()}
