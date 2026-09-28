"""Two-tier semantic cache: exact hash match, then embedding cosine match.

Tier 1: sha256 over a canonicalized request (normalized messages + model
family + tool names). Zero false positives, O(1).
Tier 2: cosine similarity over embeddings with a configurable threshold
(default 0.92) — catches paraphrases the hash cannot. LRU + TTL bounded.

High temperature (> skip threshold) and tool-calling requests bypass the
cache entirely: sampling variability and non-deterministic tool state make
replay wrong, not just stale.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
from pydantic import BaseModel

from aegisgate.clock import Clock
from aegisgate.llm.base import ChatRequest, ChatResponse

DEFAULT_EMBED_DIM = 256


class CacheConfig(BaseModel):
    enabled: bool = True
    ttl_s: float = 3600.0
    max_entries: int = 256
    semantic_threshold: float = 0.92
    skip_temperature: float = 0.7
    estimated_latency_saved_ms: float = 350.0


class CacheMetrics(BaseModel):
    hits: int = 0
    misses: int = 0
    exact_hits: int = 0
    semantic_hits: int = 0
    skips: int = 0
    latency_saved_ms: float = 0.0
    tokens_saved: int = 0


class CacheHit(BaseModel):
    tier: str  # "exact" | "semantic"
    similarity: float | None = None
    response: ChatResponse


class EmbeddingFn(Protocol):
    def __call__(self, text: str) -> np.ndarray: ...


class HashingEmbedder:
    """Deterministic bag-of-tokens hashing embedder.

    No model download, no network, stable across runs — a pragmatic offline
    stand-in for a real embedding service (swap via the EmbeddingFn seam).
    """

    def __init__(self, dim: int = DEFAULT_EMBED_DIM) -> None:
        self.dim = dim

    def __call__(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.sha1(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            vec[idx] += 1.0
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec


def canonical_request_text(request: ChatRequest, model_family: str) -> str:
    """Stable canonical form of a request for exact matching."""
    messages = [[m.role, " ".join(m.content.lower().split())] for m in request.messages]
    tools = sorted(t.name for t in request.tools) if request.tools else None
    payload = {"family": model_family, "messages": messages, "tools": tools}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@dataclass
class _Entry:
    key: str
    family: str
    text: str
    vector: np.ndarray
    response: ChatResponse
    created_at: float


@dataclass
class SemanticCache:
    config: CacheConfig
    clock: Clock
    embedder: Callable[[str], np.ndarray] = field(default_factory=HashingEmbedder)
    metrics: CacheMetrics = field(default_factory=CacheMetrics)

    def __post_init__(self) -> None:
        self._entries: OrderedDict[str, _Entry] = OrderedDict()

    # -- predicates ---------------------------------------------------------
    def skippable(self, request: ChatRequest) -> str | None:
        if not self.config.enabled:
            return "cache_disabled"
        if request.temperature > self.config.skip_temperature:
            return "high_temperature"
        if request.tools:
            return "tool_calling"
        return None

    @staticmethod
    def exact_key(request: ChatRequest, model_family: str) -> str:
        return hashlib.sha256(
            canonical_request_text(request, model_family).encode("utf-8")
        ).hexdigest()

    # -- core API -----------------------------------------------------------
    def lookup(self, request: ChatRequest, model_family: str) -> CacheHit | None:
        skip = self.skippable(request)
        if skip:
            self.metrics.skips += 1
            return None

        self._purge_expired()

        key = self.exact_key(request, model_family)
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
            self._record_hit("exact", None, entry)
            return CacheHit(tier="exact", similarity=None, response=entry.response)

        if self._is_semantic_candidate(request):
            query_vec = self.embedder(self._embed_text(request))
            best_key, best_sim = None, -1.0
            for ekey, entry in reversed(self._entries.items()):
                sim = float(np.dot(query_vec, entry.vector))
                if sim > best_sim:
                    best_key, best_sim = ekey, sim
            if best_key is not None and best_sim >= self.config.semantic_threshold:
                self._entries.move_to_end(best_key)
                entry = self._entries[best_key]
                self._record_hit("semantic", best_sim, entry)
                return CacheHit(tier="semantic", similarity=best_sim, response=entry.response)

        self.metrics.misses += 1
        return None

    def store(self, request: ChatRequest, model_family: str, response: ChatResponse) -> bool:
        if self.skippable(request):
            return False
        key = self.exact_key(request, model_family)
        entry = _Entry(
            key=key,
            family=model_family,
            text=self._embed_text(request),
            vector=self.embedder(self._embed_text(request)),
            response=response,
            created_at=self.clock.now(),
        )
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self.config.max_entries:
            self._entries.popitem(last=False)  # evict LRU
        return True

    def invalidate(self, model_family: str | None = None) -> int:
        """Drop everything, or everything for one model family. Returns count."""
        if model_family is None:
            count = len(self._entries)
            self._entries.clear()
            return count
        doomed = [k for k, e in self._entries.items() if e.family == model_family]
        for key in doomed:
            del self._entries[key]
        return len(doomed)

    def __len__(self) -> int:
        return len(self._entries)

    # -- internals ----------------------------------------------------------
    @staticmethod
    def _embed_text(request: ChatRequest) -> str:
        return " ".join(m.content for m in request.messages)

    def _is_semantic_candidate(self, request: ChatRequest) -> bool:
        return bool(request.messages) and self.config.semantic_threshold > 0

    def _purge_expired(self) -> None:
        now = self.clock.now()
        expired = [k for k, e in self._entries.items() if now - e.created_at > self.config.ttl_s]
        for key in expired:
            del self._entries[key]

    def _record_hit(self, tier: str, similarity: float | None, entry: _Entry) -> None:
        self.metrics.hits += 1
        if tier == "exact":
            self.metrics.exact_hits += 1
        else:
            self.metrics.semantic_hits += 1
        self.metrics.latency_saved_ms += self.config.estimated_latency_saved_ms
        self.metrics.tokens_saved += entry.response.usage.total_tokens
