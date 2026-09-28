"""Composite model scoring and EWMA telemetry.

Score (lower is better):

    score = w_lat * norm(latency_ema) + w_err * error_rate
          + w_cost * norm(blended_price) + w_quality * (5 - quality_tier) / 4

Latency and cost are min-max normalized across the candidate set, so the
score is scale-free. Latency and error rate are exponential moving averages
(EWMA) per model — see README for why EWMA over raw samples.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel

from aegisgate.router.registry import ModelRegistry

_EWMA_ALPHA_LATENCY = 0.3
_EWMA_ALPHA_ERROR = 0.2
_P95_WINDOW = 100


class RequestProfile(StrEnum):
    cost_sensitive = "cost_sensitive"
    balanced = "balanced"
    latency_sensitive = "latency_sensitive"
    quality_sensitive = "quality_sensitive"


@dataclass(frozen=True)
class Weights:
    latency: float
    error: float
    cost: float
    quality: float


PROFILE_WEIGHTS: dict[RequestProfile, Weights] = {
    RequestProfile.cost_sensitive: Weights(latency=0.10, error=0.20, cost=0.60, quality=0.10),
    RequestProfile.balanced: Weights(latency=0.25, error=0.25, cost=0.30, quality=0.20),
    RequestProfile.latency_sensitive: Weights(latency=0.55, error=0.25, cost=0.10, quality=0.10),
    RequestProfile.quality_sensitive: Weights(latency=0.15, error=0.20, cost=0.10, quality=0.55),
}


class ScoredModel(BaseModel):
    model: str
    score: float
    components: dict[str, float]


class LatencyErrorTracker:
    """Per-model EWMA latency, EWMA error rate, and a p95 latency window."""

    def __init__(
        self,
        *,
        default_latency_ms: float = 800.0,
        default_error_rate: float = 0.02,
        p95_window: int = _P95_WINDOW,
    ) -> None:
        self.default_latency_ms = default_latency_ms
        self.default_error_rate = default_error_rate
        self._latency_ema: dict[str, float] = {}
        self._error_ema: dict[str, float] = {}
        self._samples: dict[str, deque[float]] = {}

    def record(self, model: str, *, latency_ms: float, ok: bool) -> None:
        if model not in self._latency_ema:
            self._latency_ema[model] = self.default_latency_ms
            self._error_ema[model] = self.default_error_rate
        self._latency_ema[model] = (
            _EWMA_ALPHA_LATENCY * latency_ms + (1 - _EWMA_ALPHA_LATENCY) * self._latency_ema[model]
        )
        outcome = 0.0 if ok else 1.0
        self._error_ema[model] = (
            _EWMA_ALPHA_ERROR * outcome + (1 - _EWMA_ALPHA_ERROR) * self._error_ema[model]
        )
        window = self._samples.setdefault(model, deque(maxlen=_P95_WINDOW))
        if ok:
            window.append(latency_ms)

    def latency_ema(self, model: str) -> float:
        return self._latency_ema.get(model, self.default_latency_ms)

    def error_rate(self, model: str) -> float:
        return self._error_ema.get(model, self.default_error_rate)

    def p95(self, model: str) -> float:
        samples = self._samples.get(model)
        if not samples:
            return self.default_latency_ms
        ordered = sorted(samples)
        idx = max(0, math.ceil(0.95 * len(ordered)) - 1)
        return ordered[idx]


def _min_max(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if math.isclose(hi, lo):
        return {key: 0.0 for key in values}
    span = hi - lo
    return {key: (val - lo) / span for key, val in values.items()}


class ScoredRouter:
    def __init__(self, registry: ModelRegistry, tracker: LatencyErrorTracker) -> None:
        self.registry = registry
        self.tracker = tracker

    def route(
        self, candidate_ids: list[str], profile: RequestProfile
    ) -> list[ScoredModel]:
        """Return candidates sorted best-first for the given request profile."""
        weights = PROFILE_WEIGHTS[profile]
        specs = {cid: self.registry.get(cid) for cid in candidate_ids}
        norm_latency = _min_max({cid: self.tracker.latency_ema(cid) for cid in candidate_ids})
        norm_cost = _min_max({cid: specs[cid].blended_price for cid in candidate_ids})

        scored: list[ScoredModel] = []
        for cid in candidate_ids:
            spec = specs[cid]
            quality_term = (5 - spec.quality_tier) / 4.0
            components = {
                "latency": weights.latency * norm_latency[cid],
                "error": weights.error * self.tracker.error_rate(cid),
                "cost": weights.cost * norm_cost[cid],
                "quality": weights.quality * quality_term,
            }
            scored.append(
                ScoredModel(model=cid, score=sum(components.values()), components=components)
            )
        scored.sort(key=lambda s: s.score)
        return scored
