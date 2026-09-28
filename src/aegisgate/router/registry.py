"""Model registry: a validated YAML catalog of providers and models.

The registry is the single source of truth for prices, context windows,
capability tags and quality tiers. Routing, metering, autopilot policy and
the cache all read from it, so adding a provider is a YAML edit, not a code
change.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_REGISTRY_PATH = Path(__file__).parent / "models.yaml"


class ModelSpec(BaseModel):
    id: str
    provider: str
    family: str
    price_per_1m_input: float = Field(ge=0)
    price_per_1m_output: float = Field(ge=0)
    context_window: int = Field(gt=0)
    capabilities: list[str] = Field(default_factory=list)
    quality_tier: int = Field(ge=1, le=5)
    enabled: bool = True

    @property
    def blended_price(self) -> float:
        """Assumed 75% input / 25% output token mix for cost comparisons."""
        return 0.75 * self.price_per_1m_input + 0.25 * self.price_per_1m_output

    def cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens / 1e6) * self.price_per_1m_input + (
            completion_tokens / 1e6
        ) * self.price_per_1m_output


class ModelRegistry:
    def __init__(self, models: list[ModelSpec], version: int = 1) -> None:
        self.version = version
        self._by_id: dict[str, ModelSpec] = {}
        for model in models:
            if model.id in self._by_id:
                raise ValueError(f"duplicate model id in registry: {model.id}")
            self._by_id[model.id] = model

    @classmethod
    def load(cls, path: str | Path | None = None) -> ModelRegistry:
        registry_path = Path(path) if path else DEFAULT_REGISTRY_PATH
        raw = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
        models = [ModelSpec(**entry) for entry in raw.get("models", [])]
        return cls(models=models, version=int(raw.get("version", 1)))

    def get(self, model_id: str) -> ModelSpec:
        try:
            return self._by_id[model_id]
        except KeyError as exc:
            raise KeyError(f"unknown model: {model_id}") from exc

    def has(self, model_id: str) -> bool:
        return model_id in self._by_id

    @property
    def models(self) -> list[ModelSpec]:
        return list(self._by_id.values())

    def enabled_models(self) -> list[ModelSpec]:
        return [m for m in self._by_id.values() if m.enabled]

    def by_provider(self, provider: str) -> list[ModelSpec]:
        return [m for m in self._by_id.values() if m.provider == provider]

    def providers(self) -> list[str]:
        return sorted({m.provider for m in self._by_id.values()})

    def ids(self) -> list[str]:
        return list(self._by_id.keys())
