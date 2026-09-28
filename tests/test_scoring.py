"""Composite scoring: profile weights, EWMA telemetry, normalization."""

from __future__ import annotations

import pytest

from aegisgate.router.scoring import LatencyErrorTracker, RequestProfile, ScoredRouter


def test_cost_profile_prefers_cheapest_healthy_tier(registry):
    """Cheapest-healthy model wins under the cost profile (equal latency and
    error baselines -> cost term dominates among equal-tier models)."""
    tracker = LatencyErrorTracker()
    router = ScoredRouter(registry, tracker)
    tier3 = ["gpt-4o-mini", "llama-3.3-70b", "claude-haiku"]
    scored = router.route(tier3, RequestProfile.cost_sensitive)
    assert scored[0].model == "gpt-4o-mini"
    assert scored[0].score < scored[1].score


def test_latency_ewma_changes_ranking(registry):
    tracker = LatencyErrorTracker()
    router = ScoredRouter(registry, tracker)
    candidates = ["gpt-4o-mini", "llama-3.3-70b", "claude-haiku"]
    for _ in range(5):
        tracker.record("gpt-4o-mini", latency_ms=2000, ok=True)
    scored = router.route(candidates, RequestProfile.latency_sensitive)
    assert scored[0].model != "gpt-4o-mini"
    mini = next(s for s in scored if s.model == "gpt-4o-mini")
    # The slow model's normalized latency saturates at 1.0 * weight.
    assert mini.components["latency"] == pytest.approx(0.55)


def test_error_rate_tracking(registry):
    tracker = LatencyErrorTracker()
    router = ScoredRouter(registry, tracker)
    tracker.record("gpt-4o-mini", latency_ms=100, ok=False)
    tracker.record("gpt-4o-mini", latency_ms=100, ok=False)
    scored = router.route(["gpt-4o-mini", "llama-3.3-70b"], RequestProfile.balanced)
    mini = next(s for s in scored if s.model == "gpt-4o-mini")
    assert mini.components["error"] > 0.2 * 0.02  # well above baseline


def test_ewma_smooths_and_reacts():
    tracker = LatencyErrorTracker(default_latency_ms=800.0)
    tracker.record("m", latency_ms=1000.0, ok=True)
    expected = 0.3 * 1000 + 0.7 * 800
    assert tracker.latency_ema("m") == pytest.approx(expected)
    tracker.record("m", latency_ms=1000.0, ok=True)
    assert tracker.latency_ema("m") == pytest.approx(0.3 * 1000 + 0.7 * expected)


def test_p95_uses_observed_window():
    tracker = LatencyErrorTracker()
    for latency in [10.0, 20.0, 30.0, 40.0, 100.0]:
        tracker.record("m", latency_ms=latency, ok=True)
    assert tracker.p95("m") == 100.0
    assert tracker.p95("unknown-model") == tracker.default_latency_ms


def test_unknown_model_routing_raises(registry):
    router = ScoredRouter(registry, LatencyErrorTracker())
    with pytest.raises(KeyError):
        router.route(["not-a-model"], RequestProfile.balanced)
