"""Fallback chain: try ordered candidates until one serves, within budget.

The chain is policy-agnostic: it receives an ordered candidate list (already
scored), an availability predicate (breaker + kill switches) and an async
attempt function. It records exactly which model served and what was tried,
so every response is auditable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from aegisgate.router.hedging import HedgingConfig, execute_with_hedging


@dataclass
class AttemptRecord:
    model: str
    status: str  # "ok" | "error" | "skipped"
    error: str = ""


@dataclass
class ChainOutcome:
    served_by: str
    response: object
    attempts: list[AttemptRecord] = field(default_factory=list)
    hedged: bool = False
    hedge_winner: str = ""


class AllCandidatesFailedError(Exception):
    def __init__(self, attempts: list[AttemptRecord]) -> None:
        summary = ", ".join(f"{a.model}:{a.status}" for a in attempts)
        super().__init__(f"all candidate models failed [{summary}]")
        self.attempts = attempts


class FallbackChain:
    def __init__(self, max_attempts: int = 3) -> None:
        self.max_attempts = max_attempts

    async def run(
        self,
        candidate_ids: list[str],
        attempt_fn: Callable[[str], Awaitable],
        *,
        availability_fn: Callable[[str], bool] | None = None,
        hedge: bool = False,
        hedge_config: object | None = None,
        p95_fn: Callable[[str], float] | None = None,
    ) -> ChainOutcome:
        attempts: list[AttemptRecord] = []
        tried = 0

        def available(model: str) -> bool:
            return availability_fn is None or availability_fn(model)

        for index, model in enumerate(candidate_ids):
            if tried >= self.max_attempts:
                break
            if not available(model):
                attempts.append(AttemptRecord(model=model, status="skipped", error="unavailable"))
                continue
            tried += 1

            # Hedge the first live candidate against the next live one.
            if hedge and index == 0 and hedge_config is not None:
                shadow = next((m for m in candidate_ids[index + 1 :] if available(m)), None)
                if shadow is not None:
                    tried += 1
                    p95 = p95_fn(model) if p95_fn else 0.0
                    config = (
                        hedge_config
                        if isinstance(hedge_config, HedgingConfig)
                        else HedgingConfig()
                    )
                    result = await execute_with_hedging(
                        lambda m=model: attempt_fn(m),  # noqa: B023
                        lambda s=shadow: attempt_fn(s),  # noqa: B023
                        config=config,
                        p95_latency_ms=p95,
                    )
                    winner = model if result.winner == "primary" else shadow
                    attempts.append(AttemptRecord(model=winner, status="ok"))
                    return ChainOutcome(
                        served_by=winner,
                        response=result.value,
                        attempts=attempts,
                        hedged=True,
                        hedge_winner=result.winner,
                    )

            try:
                response = await attempt_fn(model)
            except Exception as exc:  # noqa: BLE001 - recorded and chained
                attempts.append(AttemptRecord(model=model, status="error", error=str(exc)))
                continue
            attempts.append(AttemptRecord(model=model, status="ok"))
            return ChainOutcome(served_by=model, response=response, attempts=attempts)

        raise AllCandidatesFailedError(attempts)
