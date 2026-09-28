"""Redis-backed RateLimitStore — the production multi-instance path.

Implements the ``RateLimitStore`` Protocol from ``ratelimit.py``. The redis
client is lazily imported and env-guarded: when ``redis`` (the package) or the
server is absent, callers get ``None`` from ``build_store`` and fall back to
the in-memory store — the gateway degrades to single-process instead of
failing. Buckets are serialized as JSON (schema-versioned) so a Redis-side
upgrade never bricks the limiter.
"""

from __future__ import annotations

import dataclasses
import json
import os
from typing import Any

from .ratelimit import InMemoryRateLimitStore, TokenBucket, TokenBucketConfig

_SCHEMA = "v1"


class RedisRateLimitStore:
    """Protocol-compliant store over a redis client (sync interface)."""

    def __init__(self, client: Any, prefix: str = "aegisgate:rl:") -> None:
        self._client = client
        self._prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self._prefix}{_SCHEMA}:{key}"

    def get_bucket(self, key: str) -> TokenBucket | None:
        raw = self._client.get(self._key(key))
        if not raw:
            return None
        data = json.loads(raw)
        cfg = TokenBucketConfig(**data["config"])
        # the clock is re-injected by the limiter on the next refill; persist
        # only the state that affects refill math
        bucket = TokenBucket(cfg, clock=_LastRefillClock(data["last_refill"]))
        bucket.tokens = data["tokens"]
        bucket.last_refill = data["last_refill"]
        return bucket

    def put_bucket(self, key: str, bucket: TokenBucket) -> None:
        payload = json.dumps({
            "config": dataclasses.asdict(bucket.config),
            "tokens": bucket.tokens,
            "last_refill": bucket.last_refill,
        })
        self._client.set(self._key(key), payload)

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False


def build_store(env: dict[str, str] | None = None):
    """Store factory: Redis when configured AND reachable, in-memory otherwise.

    This is the fallback contract: a missing Redis must never take the gateway
    down — it only collapses the rate-limiting domain to one process, which the
    SECURITY.md deployment notes call out.
    """
    env = env if env is not None else os.environ
    store_kind = env.get("AEGISGATE_RATELIMIT_STORE", "memory")
    if store_kind != "redis":
        return InMemoryRateLimitStore()
    url = env.get("AEGISGATE_REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis  # optional dependency

        client = redis.Redis.from_url(url, socket_connect_timeout=0.5)
        client.ping()
        return RedisRateLimitStore(client)
    except Exception:
        return InMemoryRateLimitStore()


class _LastRefillClock:
    """Frozen clock used at deserialization: the next refill computes elapsed
    against the persisted timestamp, so no time is lost across process hops."""

    def __init__(self, now: float) -> None:
        self._now = now

    def now(self) -> float:
        return self._now


class _FakeRedis:
    """Minimal redis stand-in for offline tests (get/set/ping only)."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, k: str) -> str | None:
        return self._data.get(k)

    def set(self, k: str, v: str) -> None:
        self._data[k] = v

    def ping(self) -> bool:
        return True
