"""Token-bucket rate limiting per (tenant, model).

A token bucket allows short bursts up to ``capacity`` while enforcing a
long-run rate of ``refill_per_sec`` — unlike a fixed window, which lets a
client dump 2x the limit across a window boundary and penalizes legitimate
bursts with a hard cliff. The store is a Protocol, so the in-memory default
can be swapped for Redis without touching callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

from aegisgate.clock import Clock


@dataclass
class TokenBucketConfig:
    capacity: float = 10.0
    refill_per_sec: float = 2.0


class TokenBucket:
    def __init__(self, config: TokenBucketConfig, clock: Clock) -> None:
        self.config = config
        self.clock = clock
        self.tokens = config.capacity
        self.last_refill = clock.now()

    def try_take(self, amount: float = 1.0) -> tuple[bool, float]:
        """Attempt to take tokens. Returns (allowed, retry_after_s)."""
        now = self.clock.now()
        elapsed = max(0.0, now - self.last_refill)
        self.tokens = min(self.config.capacity, self.tokens + elapsed * self.config.refill_per_sec)
        self.last_refill = now
        if self.tokens >= amount:
            self.tokens -= amount
            return True, 0.0
        needed = amount - self.tokens
        if self.config.refill_per_sec > 0:
            retry_after = needed / self.config.refill_per_sec
        else:
            retry_after = 60.0
        return False, retry_after


class RateLimitDecision(BaseModel):
    allowed: bool
    key: str
    remaining: float
    retry_after_s: float = 0.0


class RateLimitStore(Protocol):
    """Storage seam: swap the in-memory dict for Redis in production."""

    def get_bucket(self, key: str) -> TokenBucket | None: ...

    def put_bucket(self, key: str, bucket: TokenBucket) -> None: ...


class InMemoryRateLimitStore:
    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def get_bucket(self, key: str) -> TokenBucket | None:
        return self._buckets.get(key)

    def put_bucket(self, key: str, bucket: TokenBucket) -> None:
        self._buckets[key] = bucket

    def __len__(self) -> int:
        return len(self._buckets)


@dataclass
class RateLimiter:
    store: RateLimitStore
    clock: Clock
    default_config: TokenBucketConfig = field(default_factory=TokenBucketConfig)
    per_model: dict[str, TokenBucketConfig] = field(default_factory=dict)

    @staticmethod
    def key(tenant_id: str, model: str) -> str:
        return f"{tenant_id}:{model}"

    def check(self, tenant_id: str, model: str, cost: float = 1.0) -> RateLimitDecision:
        key = self.key(tenant_id, model)
        bucket = self.store.get_bucket(key)
        if bucket is None:
            config = self.per_model.get(model, self.default_config)
            bucket = TokenBucket(config, self.clock)
            self.store.put_bucket(key, bucket)
        allowed, retry_after = bucket.try_take(cost)
        return RateLimitDecision(
            allowed=allowed, key=key, remaining=max(0.0, bucket.tokens), retry_after_s=retry_after
        )
