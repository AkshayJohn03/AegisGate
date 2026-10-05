"""Central configuration via pydantic-settings.

Every knob is overridable with an ``AEGISGATE_`` prefixed environment variable
or a ``.env`` file (see ``.env.example``).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class AegisGateSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AEGISGATE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- LLM backend -------------------------------------------------------
    llm_mode: str = "echo"  # "echo" (offline) | "openai" (OpenAI-compatible)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_timeout_s: float = 30.0

    # --- Registry ----------------------------------------------------------
    registry_path: str | None = None

    # --- Auth / tenancy ----------------------------------------------------
    tenant_tokens: str = ""  # "token:tenant,token:tenant"; empty -> token IS tenant
    default_tenant: str = "public"

    # --- Rate limiting -----------------------------------------------------
    rate_limit_capacity: float = 10.0
    rate_limit_refill_per_sec: float = 2.0

    # --- Circuit breaker ---------------------------------------------------
    breaker_failure_threshold: int = 5
    breaker_recovery_timeout_s: float = 30.0
    breaker_half_open_probes: int = 2

    # --- Retry -------------------------------------------------------------
    retry_max_retries: int = 2
    retry_base_delay_s: float = 0.05
    retry_max_delay_s: float = 1.0

    # --- Hedging -----------------------------------------------------------
    hedging_enabled: bool = True
    hedge_delay_s: float = 0.25
    hedge_p95_threshold_ms: float = 1500.0

    # --- Cache -------------------------------------------------------------
    cache_enabled: bool = True
    cache_ttl_s: float = 3600.0
    cache_max_entries: int = 256
    cache_semantic_threshold: float = 0.92

    # --- Budgets (USD per tenant) ------------------------------------------
    daily_soft: float = 5.0
    daily_hard: float = 10.0
    monthly_soft: float = 50.0
    monthly_hard: float = 100.0

    # --- Flags -------------------------------------------------------------
    flags_path: str | None = None

    # --- Fallback ----------------------------------------------------------
    max_fallback_attempts: int = 3

    # --- Agent tool & MCP firewall -----------------------------------------
    tools_firewall_enabled: bool = False
    tools_policy_path: str | None = None  # YAML policy file (per-tool rules)

    # --- PII round-trip ------------------------------------------------------
    pii_mode: str = "off"  # "off" | "anonymize"

    # --- Server ------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000
