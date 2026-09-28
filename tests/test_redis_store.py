"""Redis store: the production multi-instance path, tested offline via a fake."""

from aegisgate.ratelimit import RateLimiter, TokenBucketConfig
from aegisgate.redis_store import RedisRateLimitStore, _FakeRedis, build_store


class _ManualClock:
    def __init__(self) -> None:
        self._t = 1000.0

    def now(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        self._t += dt


def _fresh_limiter(store):
    clock = _ManualClock()
    limiter = RateLimiter(store=store, clock=clock,
                          default_config=TokenBucketConfig(capacity=3, refill_per_sec=1.0))
    return limiter, clock


def test_redis_store_roundtrip_and_limiting():
    fake = _FakeRedis()
    limiter, clock = _fresh_limiter(RedisRateLimitStore(fake))

    ok1 = limiter.check("acme", "gpt-4o").allowed
    ok2 = limiter.check("acme", "gpt-4o").allowed
    ok3 = limiter.check("acme", "gpt-4o").allowed
    d4 = limiter.check("acme", "gpt-4o")
    ok4, retry = d4.allowed, d4.retry_after_s
    assert (ok1, ok2, ok3) == (True, True, True)
    assert not ok4 and retry > 0

    # a second limiter over the SAME redis sees the same bucket (multi-instance)
    limiter2, clock2 = _fresh_limiter(RedisRateLimitStore(fake))
    clock2.advance(5.0)
    limiter2.clock = clock2
    allowed_after_refill = limiter2.check("acme", "gpt-4o").allowed
    assert allowed_after_refill, "refill must survive the process hop"


def test_build_store_falls_back_without_redis():
    # configured for redis but unreachable -> in-memory fallback, never a crash
    store = build_store({"AEGISGATE_RATELIMIT_STORE": "redis",
                         "AEGISGATE_REDIS_URL": "redis://localhost:1/0"})
    assert store is not None
    limiter, clock = _fresh_limiter(store)
    ok = limiter.check("acme", "gpt-4o").allowed
    assert ok


def test_build_store_memory_default():
    store = build_store({})
    assert type(store).__name__ == "InMemoryRateLimitStore"
