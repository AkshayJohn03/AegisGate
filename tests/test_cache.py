"""Two-tier semantic cache: exact hits, semantic hits, skips, TTL, LRU."""

from __future__ import annotations

import numpy as np

from aegisgate.cache.semantic import (
    CacheConfig,
    HashingEmbedder,
    SemanticCache,
    canonical_request_text,
)
from aegisgate.clock import ManualClock
from aegisgate.llm.base import ChatResponse, ToolSpec, Usage


def make_response(content: str = "cached answer") -> ChatResponse:
    return ChatResponse(
        content=content,
        model="gpt-4o",
        usage=Usage(prompt_tokens=10, completion_tokens=5),
    )


class ConstantEmbedder:
    """Every text maps to the same unit vector -> guaranteed semantic match."""

    def __call__(self, text: str) -> np.ndarray:
        vec = np.ones(8, dtype=np.float32)
        return vec / np.linalg.norm(vec)


def test_exact_cache_hit(make_request):
    cache = SemanticCache(CacheConfig(), ManualClock())
    request = make_request(content="what is the capital of France")
    assert cache.lookup(request, "gpt-4o") is None
    cache.store(request, "gpt-4o", make_response())
    hit = cache.lookup(request, "gpt-4o")
    assert hit is not None and hit.tier == "exact"
    assert hit.response.content == "cached answer"
    assert cache.metrics.exact_hits == 1


def test_semantic_cache_hit_when_same_embedding(make_request):
    cache = SemanticCache(CacheConfig(), ManualClock(), embedder=ConstantEmbedder())
    first = make_request(content="how do I reset my password")
    second = make_request(content="password reset steps please")  # differs -> different hash
    assert canonical_request_text(first, "gpt-4o") != canonical_request_text(second, "gpt-4o")
    cache.store(first, "gpt-4o", make_response("reset via settings"))
    hit = cache.lookup(second, "gpt-4o")
    assert hit is not None and hit.tier == "semantic"
    assert hit.similarity >= 0.92
    assert cache.metrics.semantic_hits == 1


def test_no_semantic_hit_below_threshold(make_request):
    cache = SemanticCache(
        CacheConfig(semantic_threshold=0.99), ManualClock(), embedder=HashingEmbedder()
    )
    cache.store(make_request(content="whats the weather today"), "gpt-4o", make_response())
    unrelated = make_request(content="completely unrelated quantum physics")
    assert cache.lookup(unrelated, "gpt-4o") is None
    assert cache.metrics.misses == 1


def test_high_temperature_bypasses_cache(make_request):
    cache = SemanticCache(CacheConfig(), ManualClock())
    request = make_request(content="be creative", temperature=0.9)
    cache.store(request, "gpt-4o", make_response())  # store refused too
    assert len(cache) == 0
    assert cache.lookup(request, "gpt-4o") is None
    assert cache.metrics.skips == 1  # skips counted on the lookup path


def test_tool_calling_requests_bypass_cache(make_request):
    cache = SemanticCache(CacheConfig(), ManualClock())
    request = make_request(content="list files", tools=[ToolSpec(name="list_dir")])
    assert cache.lookup(request, "gpt-4o") is None
    assert not cache.store(request, "gpt-4o", make_response())


def test_ttl_expiry(make_request):
    clock = ManualClock()
    cache = SemanticCache(CacheConfig(ttl_s=100.0), clock)
    request = make_request(content="hello")
    cache.store(request, "gpt-4o", make_response())
    assert cache.lookup(request, "gpt-4o") is not None
    clock.advance(101.0)
    assert cache.lookup(request, "gpt-4o") is None


def test_lru_eviction(make_request):
    cache = SemanticCache(CacheConfig(max_entries=2), ManualClock())
    for content in ["one", "two", "three"]:
        cache.store(make_request(content=content), "gpt-4o", make_response(content))
    assert len(cache) == 2
    assert cache.lookup(make_request(content="one"), "gpt-4o") is None  # oldest evicted
    assert cache.lookup(make_request(content="three"), "gpt-4o") is not None


def test_invalidation(make_request):
    cache = SemanticCache(CacheConfig(), ManualClock())
    cache.store(make_request(content="a question", model="gpt-4o"), "gpt-4o", make_response())
    haiku_request = make_request(content="another", model="claude-haiku")
    cache.store(haiku_request, "claude-haiku", make_response())
    assert cache.invalidate("gpt-4o") == 1
    assert cache.lookup(make_request(content="a question", model="gpt-4o"), "gpt-4o") is None
    assert cache.lookup(haiku_request, "claude-haiku") is not None
    assert cache.invalidate() == 1
    assert len(cache) == 0


def test_metrics_track_savings(make_request):
    cache = SemanticCache(CacheConfig(), ManualClock())
    request = make_request(content="metric me")
    cache.store(request, "gpt-4o", make_response())
    cache.lookup(request, "gpt-4o")
    cache.lookup(make_request(content="miss please"), "gpt-4o")
    assert cache.metrics.hits == 1
    assert cache.metrics.misses == 1
    assert cache.metrics.tokens_saved == 15
    assert cache.metrics.latency_saved_ms > 0
