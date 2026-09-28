"""Health monitor: EWMA anomaly detection and auto-quarantine."""

from __future__ import annotations

from aegisgate.clock import ManualClock
from aegisgate.flags import FeatureFlags
from aegisgate.router.breaker import BreakerRegistry, BreakerState, CircuitBreakerConfig
from aegisgate.router.registry import ModelRegistry
from aegisgate.selfheal.monitor import HealthConfig, HealthMonitor


def build_monitor(clock: ManualClock, alerts: list):
    registry = ModelRegistry.load()
    breakers = BreakerRegistry(CircuitBreakerConfig(), clock)
    flags = FeatureFlags()
    monitor = HealthMonitor(
        registry,
        breakers,
        flags,
        clock=clock,
        config=HealthConfig(min_samples=3, anomaly_threshold=0.45, ewma_alpha=0.5),
        alert_sink=alerts.append,
    )
    return monitor, breakers, flags


def test_quarantine_after_error_spike(clock):
    alerts: list[dict] = []
    monitor, breakers, flags = build_monitor(clock, alerts)
    for _ in range(3):
        monitor.record("openai", ok=False)
    assert monitor.is_quarantined("openai")
    # every openai model is kill-switched and the provider breaker is open
    assert flags.is_killed("gpt-4o")
    assert flags.is_killed("gpt-4o-mini")
    assert breakers.get("openai").state is BreakerState.open
    # alert payload emitted exactly once with evidence
    assert len(alerts) == 1
    payload = alerts[0]
    assert payload["event"] == "provider.quarantined"
    assert payload["provider"] == "openai"
    assert set(payload["models_disabled"]) == {"gpt-4o", "gpt-4o-mini", "o3-mini"}
    assert "error_ewma" in payload and "timestamp" in payload


def test_healthy_traffic_does_not_quarantine(clock):
    alerts: list[dict] = []
    monitor, _, _ = build_monitor(clock, alerts)
    for _ in range(20):
        monitor.record("anthropic", ok=True)
    assert not monitor.is_quarantined("anthropic")
    assert alerts == []


def test_few_failures_below_min_samples_no_quarantine(clock):
    alerts: list[dict] = []
    monitor, breakers, flags = build_monitor(clock, alerts)
    monitor.record("google", ok=False)
    monitor.record("google", ok=False)
    assert not monitor.is_quarantined("google")
    assert not flags.is_killed("gemini-2.0-flash")
    assert breakers.get("google").state is BreakerState.closed


def test_unquarantine_restores_routing(clock):
    alerts: list[dict] = []
    monitor, breakers, flags = build_monitor(clock, alerts)
    for _ in range(3):
        monitor.record("openai", ok=False)
    assert flags.is_killed("gpt-4o")
    monitor.unquarantine("openai")
    assert not flags.is_killed("gpt-4o")
    assert not monitor.is_quarantined("openai")
