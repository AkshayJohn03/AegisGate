"""Circuit breaker state machine, driven by a manual clock."""

from __future__ import annotations

from aegisgate.clock import ManualClock
from aegisgate.router.breaker import (
    BreakerRegistry,
    BreakerState,
    CircuitBreaker,
    CircuitBreakerConfig,
)


def make_breaker(clock: ManualClock, **kwargs) -> CircuitBreaker:
    config = CircuitBreakerConfig(
        failure_threshold=kwargs.get("failure_threshold", 3),
        recovery_timeout_s=kwargs.get("recovery_timeout_s", 10.0),
        half_open_max_probes=kwargs.get("half_open_max_probes", 2),
    )
    return CircuitBreaker("openai", config, clock)


def test_breaker_opens_after_threshold(clock):
    breaker = make_breaker(clock)
    assert breaker.allow_request()
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state is BreakerState.open
    assert not breaker.allow_request()


def test_half_open_probe_quota_then_close(clock):
    breaker = make_breaker(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.advance(10.0)
    # recovery timeout elapsed: allow probes up to the quota
    assert breaker.allow_request()
    assert breaker.state is BreakerState.half_open
    assert breaker.allow_request()
    assert not breaker.allow_request()  # probe quota exhausted
    breaker.record_success()
    assert breaker.state is BreakerState.closed
    assert breaker.allow_request()


def test_half_open_probe_failure_reopens(clock):
    breaker = make_breaker(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.advance(10.0)
    assert breaker.allow_request()
    breaker.record_failure()
    assert breaker.state is BreakerState.open
    clock.advance(5.0)
    assert not breaker.allow_request()  # still within the new timeout window
    clock.advance(5.0)
    assert breaker.allow_request()


def test_success_resets_failure_count(clock):
    breaker = make_breaker(clock)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is BreakerState.closed  # threshold never reached
    breaker.record_failure()
    assert breaker.state is BreakerState.open


def test_trip_forces_open(clock):
    breaker = make_breaker(clock)
    breaker.trip()
    assert breaker.state is BreakerState.open
    assert not breaker.allow_request()


def test_registry_shares_one_breaker_per_provider(clock):
    registry = BreakerRegistry(CircuitBreakerConfig(failure_threshold=1), clock)
    assert registry.get("openai") is registry.get("openai")
    registry.record_failure("openai")
    assert registry.get("openai").state is BreakerState.open
    snapshot = registry.snapshot()
    assert snapshot["openai"]["state"] == "open"
