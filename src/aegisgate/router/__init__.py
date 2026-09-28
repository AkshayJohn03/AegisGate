"""Routing: registry, scoring, resilience primitives."""

from aegisgate.router.breaker import (
    BreakerRegistry,
    BreakerState,
    CircuitBreaker,
    CircuitBreakerConfig,
)
from aegisgate.router.fallback import (
    AllCandidatesFailedError,
    AttemptRecord,
    ChainOutcome,
    FallbackChain,
)
from aegisgate.router.hedging import HedgeResult, HedgingConfig, execute_with_hedging
from aegisgate.router.registry import ModelRegistry, ModelSpec
from aegisgate.router.retry import RetryPolicy, execute_with_retry
from aegisgate.router.scoring import (
    LatencyErrorTracker,
    RequestProfile,
    ScoredModel,
    ScoredRouter,
    Weights,
)

__all__ = [
    "AllCandidatesFailedError",
    "AttemptRecord",
    "BreakerRegistry",
    "BreakerState",
    "ChainOutcome",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "FallbackChain",
    "HedgeResult",
    "HedgingConfig",
    "LatencyErrorTracker",
    "ModelRegistry",
    "ModelSpec",
    "RequestProfile",
    "RetryPolicy",
    "ScoredModel",
    "ScoredRouter",
    "Weights",
    "execute_with_hedging",
    "execute_with_retry",
]
