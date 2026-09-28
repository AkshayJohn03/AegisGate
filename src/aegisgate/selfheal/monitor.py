"""Health monitor: EWMA error-rate anomaly detection with auto-quarantine.

Each provider's error outcome streams through an EWMA. When the smoothed
error rate crosses the anomaly threshold (after a minimum sample count), the
monitor quarantines the provider automatically:

1. trips its circuit breaker (open, no traffic);
2. sets kill switches on every model of that provider (routing excludes them
   even if a stale scorer would still pick one);
3. emits a webhook-shaped alert payload to the configured sink (offline: the
   payload is recorded; in prod: POST to an alerts endpoint).

Quarantine is reversible via ``unquarantine`` once an operator (or a
recovery probe) confirms the provider is healthy again.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from aegisgate.clock import Clock
from aegisgate.flags import FeatureFlags
from aegisgate.router.breaker import BreakerRegistry
from aegisgate.router.registry import ModelRegistry


class HealthConfig(BaseModel):
    ewma_alpha: float = 0.3
    baseline_error_rate: float = 0.02
    anomaly_threshold: float = 0.45
    min_samples: int = 5


@dataclass
class ProviderHealth:
    provider: str
    error_ewma: float
    samples: int = 0
    quarantined: bool = False


class HealthMonitor:
    def __init__(
        self,
        registry: ModelRegistry,
        breakers: BreakerRegistry,
        flags: FeatureFlags,
        *,
        clock: Clock,
        config: HealthConfig | None = None,
        alert_sink: Callable[[dict], None] | None = None,
    ) -> None:
        self.registry = registry
        self.breakers = breakers
        self.flags = flags
        self.clock = clock
        self.config = config or HealthConfig()
        self.alert_sink = alert_sink
        self._health: dict[str, ProviderHealth] = {}
        self.events: list[dict] = []

    def record(self, provider: str, *, ok: bool) -> None:
        health = self._health.setdefault(
            provider,
            ProviderHealth(provider=provider, error_ewma=self.config.baseline_error_rate),
        )
        outcome = 0.0 if ok else 1.0
        health.samples += 1
        health.error_ewma = (
            self.config.ewma_alpha * outcome
            + (1 - self.config.ewma_alpha) * health.error_ewma
        )
        if (
            not health.quarantined
            and health.samples >= self.config.min_samples
            and health.error_ewma >= self.config.anomaly_threshold
        ):
            self.quarantine(provider, health)

    def quarantine(self, provider: str, health: ProviderHealth | None = None) -> dict:
        health = health or self._health.setdefault(
            provider, ProviderHealth(provider=provider, error_ewma=self.config.anomaly_threshold)
        )
        health.quarantined = True
        self.breakers.get(provider).trip()
        models = [m.id for m in self.registry.by_provider(provider)]
        for model_id in models:
            self.flags.set_kill_switch(model_id, True)
        payload = {
            "event": "provider.quarantined",
            "provider": provider,
            "reason": f"EWMA error rate {health.error_ewma:.2f} >= "
            f"threshold {self.config.anomaly_threshold:.2f} "
            f"over {health.samples} samples",
            "error_ewma": round(health.error_ewma, 4),
            "models_disabled": models,
            "timestamp": datetime.fromtimestamp(self.clock.now(), tz=UTC).isoformat(),
            "recommended_action": "verify provider status; call unquarantine() to recover",
        }
        self.events.append(payload)
        if self.alert_sink is not None:
            self.alert_sink(payload)
        return payload

    def unquarantine(self, provider: str) -> None:
        health = self._health.get(provider)
        if health is not None:
            health.quarantined = False
            health.error_ewma = self.config.baseline_error_rate
            health.samples = 0
        for model in self.registry.by_provider(provider):
            self.flags.set_kill_switch(model.id, False)

    def is_quarantined(self, provider: str) -> bool:
        health = self._health.get(provider)
        return bool(health and health.quarantined)

    def snapshot(self) -> dict[str, dict]:
        return {
            provider: {
                "error_ewma": round(h.error_ewma, 4),
                "samples": h.samples,
                "quarantined": h.quarantined,
            }
            for provider, h in self._health.items()
        }
