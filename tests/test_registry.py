"""Registry loading and validation."""

from __future__ import annotations

import pytest

from aegisgate.router.registry import ModelRegistry, ModelSpec

EXPECTED_IDS = {
    "gpt-4o",
    "gpt-4o-mini",
    "o3-mini",
    "claude-sonnet-4",
    "claude-haiku",
    "gemini-2.0-flash",
    "deepseek-chat",
    "llama-3.3-70b",
    "mistral-large",
    "grok-2",
    "qwen-max",
    "nova-pro",
}


def test_bundled_registry_loads_12_models(registry: ModelRegistry):
    assert set(registry.ids()) == EXPECTED_IDS
    assert len(registry.providers()) >= 9


def test_model_fields_are_realistic(registry: ModelRegistry):
    spec = registry.get("gpt-4o")
    assert spec.provider == "openai"
    assert spec.price_per_1m_input == pytest.approx(2.50)
    assert spec.price_per_1m_output == pytest.approx(10.00)
    assert spec.context_window == 128_000
    assert "tools" in spec.capabilities
    assert 1 <= spec.quality_tier <= 5
    assert spec.family == "gpt-4o"


def test_unknown_model_raises(registry: ModelRegistry):
    with pytest.raises(KeyError):
        registry.get("gpt-9-nonexistent")


def test_cost_computation(registry: ModelRegistry):
    spec = registry.get("gpt-4o-mini")
    cost = spec.cost_usd(1_000_000, 1_000_000)
    assert cost == pytest.approx(0.15 + 0.60)


def test_quality_tier_bounds_enforced():
    with pytest.raises(ValueError):
        ModelSpec(
            id="bad",
            provider="x",
            family="x",
            price_per_1m_input=1,
            price_per_1m_output=1,
            context_window=1000,
            quality_tier=7,
        )


def test_custom_registry_path(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(
        """
version: 1
models:
  - id: test-model
    provider: testco
    family: test
    price_per_1m_input: 1.0
    price_per_1m_output: 2.0
    context_window: 4096
    capabilities: [chat]
    quality_tier: 2
""",
        encoding="utf-8",
    )
    registry = ModelRegistry.load(path)
    assert registry.get("test-model").provider == "testco"
