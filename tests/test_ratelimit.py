"""Token-bucket rate limiting per (tenant, model), manual-clock driven."""

from __future__ import annotations

from aegisgate.clock import ManualClock
from aegisgate.ratelimit import (
    InMemoryRateLimitStore,
    RateLimiter,
    TokenBucket,
    TokenBucketConfig,
)


def test_bucket_burst_then_reject():
    clock = ManualClock()
    bucket = TokenBucket(TokenBucketConfig(capacity=2.0, refill_per_sec=1.0), clock)
    assert bucket.try_take() == (True, 0.0)
    assert bucket.try_take() == (True, 0.0)
    allowed, retry_after = bucket.try_take()
    assert not allowed
    assert retry_after > 0.99  # one full token must refill


def test_bucket_refills_over_time():
    clock = ManualClock()
    bucket = TokenBucket(TokenBucketConfig(capacity=2.0, refill_per_sec=1.0), clock)
    bucket.try_take()
    bucket.try_take()
    allowed, retry_after = bucket.try_take()
    assert not allowed
    clock.advance(0.5)
    allowed, retry_after = bucket.try_take()
    assert not allowed
    assert retry_after == 0.5  # half a token short
    clock.advance(0.5)
    allowed, _ = bucket.try_take()
    assert allowed


def test_bucket_capacity_is_ceiling():
    clock = ManualClock()
    bucket = TokenBucket(TokenBucketConfig(capacity=2.0, refill_per_sec=10.0), clock)
    clock.advance(100.0)  # long idle: still capped at capacity
    allowed, _ = bucket.try_take(2.0)
    assert allowed
    allowed, _ = bucket.try_take(1.0)
    assert not allowed


def test_rate_limiter_keys_per_tenant_and_model():
    clock = ManualClock()
    limiter = RateLimiter(
        InMemoryRateLimitStore(),
        clock,
        default_config=TokenBucketConfig(capacity=1.0, refill_per_sec=0.0001),
    )
    first = limiter.check("acme", "gpt-4o")
    assert first.allowed
    second = limiter.check("acme", "gpt-4o")
    assert not second.allowed
    assert second.retry_after_s > 0
    # other tenant / other model: independent bucket
    assert limiter.check("globex", "gpt-4o").allowed
    assert limiter.check("acme", "claude-haiku").allowed
    assert limiter.check("acme", "gpt-4o").key == "acme:gpt-4o"
