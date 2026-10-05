"""The orchestration every request flows through.

    rate limit -> flags/kill-switch -> cache lookup -> autopilot routing
      -> breaker/retry/hedge/fallback execution -> usage metering
      -> cache store

Every stage is instrumented twice: structured log records (a list on the
result, plus a pluggable log_sink) and ForensiQ-compatible span dicts
({"span_id","parent_id","name","stage","duration_ms","status","attrs"})
emitted to a pluggable ``span_sink`` callable (no-op by default). That sink
is the integration hook for ForensiQ-style forensic tracing.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from uuid import uuid4

from aegisgate.cache.semantic import CacheConfig, SemanticCache
from aegisgate.clock import Clock, SystemClock
from aegisgate.config import AegisGateSettings
from aegisgate.cost.autopilot import AutopilotConfig, CostAutopilot
from aegisgate.cost.meter import Budget, CostMeter
from aegisgate.flags import FeatureFlags
from aegisgate.gateway.metrics import Metrics
from aegisgate.llm.base import ChatRequest, ChatResponse, LLMClient, Usage
from aegisgate.mcp_firewall import ToolFirewall, load_policy
from aegisgate.pii import PIIRoundTrip
from aegisgate.ratelimit import InMemoryRateLimitStore, RateLimiter, TokenBucketConfig
from aegisgate.router.breaker import BreakerRegistry, CircuitBreakerConfig
from aegisgate.router.fallback import AllCandidatesFailedError, AttemptRecord, FallbackChain
from aegisgate.router.hedging import HedgingConfig
from aegisgate.router.registry import ModelRegistry
from aegisgate.router.retry import RetryPolicy, execute_with_retry
from aegisgate.router.scoring import LatencyErrorTracker, RequestProfile, ScoredRouter
from aegisgate.selfheal.monitor import HealthConfig, HealthMonitor

logger = logging.getLogger("aegisgate.pipeline")

SpanSink = Callable[[dict[str, Any]], None]
LogSink = Callable[[dict[str, Any]], None]


class PipelineError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after_s = retry_after_s


@dataclass
class PipelineResult:
    trace_id: str
    response: ChatResponse | None = None
    error: PipelineError | None = None
    served_by: str = ""
    cache_tier: str | None = None
    attempts: list[AttemptRecord] = field(default_factory=list)
    hedged: bool = False
    cost_usd: float = 0.0
    spans: list[dict[str, Any]] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    tool_firewall: list[dict[str, Any]] | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.response is not None

    @property
    def tool_firewall_blocked(self) -> bool:
        """True when the tool firewall denied at least one model tool call."""
        return any(d.get("verdict") == "deny" for d in (self.tool_firewall or []))


@dataclass
class GatewayPipeline:
    settings: AegisGateSettings
    registry: ModelRegistry
    client: LLMClient
    clock: Clock = field(default_factory=SystemClock)
    metrics: Metrics = field(default_factory=Metrics)
    span_sink: SpanSink | None = None
    log_sink: LogSink | None = None

    def __post_init__(self) -> None:
        s = self.settings
        self.tracker = LatencyErrorTracker()
        self.router = ScoredRouter(self.registry, self.tracker)
        self.breakers = BreakerRegistry(
            CircuitBreakerConfig(
                failure_threshold=s.breaker_failure_threshold,
                recovery_timeout_s=s.breaker_recovery_timeout_s,
                half_open_max_probes=s.breaker_half_open_probes,
            ),
            self.clock,
        )
        self.limiter = RateLimiter(
            InMemoryRateLimitStore(),
            self.clock,
            default_config=TokenBucketConfig(
                capacity=s.rate_limit_capacity, refill_per_sec=s.rate_limit_refill_per_sec
            ),
        )
        self.cache = SemanticCache(
            CacheConfig(
                enabled=s.cache_enabled,
                ttl_s=s.cache_ttl_s,
                max_entries=s.cache_max_entries,
                semantic_threshold=s.cache_semantic_threshold,
            ),
            self.clock,
        )
        self.flags = FeatureFlags(store_path=s.flags_path)
        self.meter = CostMeter(
            self.registry,
            self.clock,
            default_budget=Budget(
                daily_soft=s.daily_soft,
                daily_hard=s.daily_hard,
                monthly_soft=s.monthly_soft,
                monthly_hard=s.monthly_hard,
            ),
        )
        self.autopilot = CostAutopilot(self.meter, config=AutopilotConfig())
        self.monitor = HealthMonitor(
            self.registry, self.breakers, self.flags, clock=self.clock, config=HealthConfig()
        )
        self.chain = FallbackChain(max_attempts=s.max_fallback_attempts)
        self.hedge_config = HedgingConfig(
            enabled=s.hedging_enabled,
            hedge_delay_s=s.hedge_delay_s,
            p95_threshold_ms=s.hedge_p95_threshold_ms,
        )
        self.retry_policy = RetryPolicy(
            max_retries=s.retry_max_retries,
            base_delay_s=s.retry_base_delay_s,
            max_delay_s=s.retry_max_delay_s,
        )
        self.firewall: ToolFirewall | None = None
        if s.tools_firewall_enabled:
            config = load_policy(s.tools_policy_path) if s.tools_policy_path else None
            self.firewall = ToolFirewall(config)
        self.pii = PIIRoundTrip() if s.pii_mode == "anonymize" else None
        self._log = self.log_sink or (lambda record: logger.info(str(record)))

    # -- instrumentation ------------------------------------------------------
    def _emit_span(
        self,
        spans: list[dict[str, Any]],
        parent_id: str,
        name: str,
        status: str,
        duration_ms: float,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        span = {
            "span_id": uuid4().hex[:12],
            "parent_id": parent_id,
            "name": name,
            "stage": name,
            "duration_ms": round(duration_ms, 3),
            "status": status,
            "attrs": dict(attrs or {}),
        }
        spans.append(span)
        if self.span_sink is not None:
            self.span_sink(span)

    @asynccontextmanager
    async def _stage(
        self,
        name: str,
        spans: list[dict[str, Any]],
        parent_id: str,
        attrs: dict[str, Any] | None = None,
    ) -> AsyncIterator[None]:
        start = perf_counter()
        status, error_attr = "ok", None
        try:
            yield
        except PipelineError as exc:
            status, error_attr = "error", exc.code
            raise
        except Exception as exc:  # noqa: BLE001
            status, error_attr = "error", type(exc).__name__
            raise
        finally:
            span_attrs = {**(attrs or {})}
            if error_attr:
                span_attrs["error"] = error_attr
            self._emit_span(
                spans, parent_id, name, status, (perf_counter() - start) * 1000, span_attrs
            )

    def _log_record(self, logs: list[dict], stage: str, message: str, **fields: Any) -> None:
        record = {"ts": self.clock.now(), "stage": stage, "msg": message, **fields}
        logs.append(record)
        self._log(record)

    # -- execution helpers ------------------------------------------------------
    def _make_attempt_fn(self, request: ChatRequest, tenant_id: str, logs: list[dict]):
        async def attempt(model_id: str) -> ChatResponse:
            spec = self.registry.get(model_id)
            model_request = request.model_copy(update={"model": model_id})
            start = perf_counter()
            try:
                response = await execute_with_retry(
                    lambda: self.client.complete(model_request), self.retry_policy
                )
            except Exception as exc:  # noqa: BLE001
                latency = (perf_counter() - start) * 1000
                self.breakers.record_failure(spec.provider)
                self.tracker.record(model_id, latency_ms=latency, ok=False)
                self.monitor.record(spec.provider, ok=False)
                self._log_record(
                    logs, "execute", "model attempt failed", model=model_id, error=str(exc)
                )
                raise
            latency = (perf_counter() - start) * 1000
            self.breakers.record_success(spec.provider)
            self.tracker.record(model_id, latency_ms=response.latency_ms or latency, ok=True)
            self.monitor.record(spec.provider, ok=True)
            return response

        return attempt

    def _availability(self) -> Callable[[str], bool]:
        def available(model_id: str) -> bool:
            if not self.flags.is_model_available(model_id):
                return False
            return self.breakers.allow(self.registry.get(model_id).provider)

        return available

    async def _mirror_shadow(
        self, request: ChatRequest, served_model: str, primary: ChatResponse, logs: list[dict]
    ) -> None:
        target = self.flags.shadow_target(served_model)
        if target is None:
            return
        try:
            shadow_response = await execute_with_retry(
                lambda: self.client.complete(request.model_copy(update={"model": target})),
                self.retry_policy,
            )
            self.flags.record_shadow_diff(
                model=served_model,
                candidate=target,
                prompt=request.messages[-1].content if request.messages else "",
                primary_output=primary.content,
                candidate_output=shadow_response.content,
            )
            self._log_record(
                logs, "shadow", "shadow diff recorded", model=served_model, shadow=target
            )
        except Exception as exc:  # noqa: BLE001 - shadow never affects the user path
            self._log_record(logs, "shadow", "shadow failed", model=served_model, error=str(exc))

    @staticmethod
    def _tags(tenant: str, model: str) -> dict[str, str]:
        return {"tenant": tenant, "model": model}

    def _candidates(self, request: ChatRequest, adjustment, *, premium: bool) -> list[str]:
        candidates = [
            spec.id
            for spec in self.registry.enabled_models()
            if adjustment.admits(spec) and not self.flags.is_killed(spec.id)
        ]
        if not self.flags.is_killed(request.model) and adjustment.admits(
            self.registry.get(request.model)
        ):
            candidates = [request.model] + [c for c in candidates if c != request.model]
        return candidates

    # -- non-streaming handle -----------------------------------------------------
    async def handle(
        self, request: ChatRequest, tenant_id: str, *, premium: bool = False
    ) -> PipelineResult:
        trace_id = uuid4().hex[:16]
        spans: list[dict[str, Any]] = []
        logs: list[dict[str, Any]] = []
        result = PipelineResult(trace_id=trace_id)
        self._log_record(
            logs,
            "request",
            "chat completion",
            tenant=tenant_id,
            model=request.model,
            premium=premium,
        )
        try:
            # 1. rate limit -------------------------------------------------------
            async with self._stage("rate_limit", spans, trace_id, {"tenant": tenant_id}):
                decision = self.limiter.check(tenant_id, request.model)
                if not decision.allowed:
                    self.metrics.inc("aegisgate_rate_limited_total")
                    raise PipelineError(
                        429,
                        "rate_limited",
                        f"rate limit exceeded for {decision.key}",
                        retry_after_s=decision.retry_after_s,
                    )

            # 2. flags / kill switch -----------------------------------------------
            requested_killed = self.flags.is_killed(request.model)
            async with self._stage("flags", spans, trace_id, {"model_killed": requested_killed}):
                if requested_killed:
                    self._log_record(
                        logs,
                        "flags",
                        "requested model kill-switched; rerouting",
                        model=request.model,
                    )

            # 1b. PII anonymize (optional) ------------------------------------------
            # Runs before the cache lookup so cached entries and everything
            # downstream (autopilot, shadow, provider) only ever see tokens.
            pii_map: dict[str, str] = {}
            if self.pii is not None:
                async with self._stage("pii_anonymize", spans, trace_id):
                    redactions = 0
                    pseudonymized = []
                    for message in request.messages:
                        sanitized, mapping = self.pii.pseudonymize(message.content)
                        redactions += len(mapping)
                        pii_map.update(mapping)
                        pseudonymized.append(message.model_copy(update={"content": sanitized}))
                    if redactions:
                        request = request.model_copy(update={"messages": pseudonymized})
                        self.metrics.inc("aegisgate_pii_redactions_total", value=redactions)
                    self._log_record(
                        logs, "pii", "request pseudonymized", redactions=redactions
                    )

            # 3. cache lookup ---------------------------------------------------------
            family = self.registry.get(request.model).family
            async with self._stage("cache_lookup", spans, trace_id):
                hit = self.cache.lookup(request, family)

            if hit is not None:
                self.metrics.inc("aegisgate_cache_hits_total", {"tier": hit.tier})
                cached_response = hit.response.model_copy()
                if self.pii is not None and pii_map:
                    cached_response.content = self.pii.restore(cached_response.content, pii_map)
                result.response = cached_response
                result.served_by = f"cache:{hit.tier}"
                result.cache_tier = hit.tier
                self._log_record(
                    logs,
                    "cache",
                    "cache hit",
                    tier=hit.tier,
                    similarity=round(hit.similarity, 4) if hit.similarity else None,
                )
                self.metrics.inc(
                    "aegisgate_requests_total",
                    self._tags(tenant_id, request.model) | {"status": "ok"},
                )
                result.spans, result.logs = spans, logs
                return result
            self.metrics.inc("aegisgate_cache_misses_total")

            # 4. autopilot ------------------------------------------------------------
            async with self._stage("autopilot", spans, trace_id):
                adjustment = self.autopilot.adjust(
                    tenant_id, request.messages, premium=premium, has_tools=bool(request.tools)
                )
                if not adjustment.allow:
                    raise PipelineError(
                        429, "budget_exceeded", adjustment.reason or "budget exhausted"
                    )
            self._log_record(
                logs, "autopilot", adjustment.reason, complexity=adjustment.complexity.value
            )

            # 5. route ------------------------------------------------------------------
            async with self._stage("route", spans, trace_id, {"profile": adjustment.profile_hint}):
                candidates = self._candidates(request, adjustment, premium=premium)
                if not candidates:
                    raise PipelineError(
                        503,
                        "no_model_available",
                        "no candidate models available for this request",
                    )
                profile = (
                    RequestProfile.quality_sensitive
                    if premium
                    else RequestProfile(adjustment.profile_hint)
                )
                scored = self.router.route(candidates, profile)

            # 6. execute: breaker + retry + hedge + fallback ------------------------------
            async with self._stage("execute", spans, trace_id, {"candidates": len(candidates)}):
                try:
                    outcome = await self.chain.run(
                        [s.model for s in scored],
                        self._make_attempt_fn(request, tenant_id, logs),
                        availability_fn=self._availability(),
                        hedge=self.hedge_config.enabled,
                        hedge_config=self.hedge_config,
                        p95_fn=self.tracker.p95,
                    )
                except AllCandidatesFailedError as exc:
                    raise PipelineError(
                        503, "no_model_available", "all candidate models failed"
                    ) from exc
            response: ChatResponse = outcome.response  # type: ignore[assignment]
            result.response = response
            result.served_by = outcome.served_by
            result.attempts = outcome.attempts
            result.hedged = outcome.hedged

            # 6b. agent tool & MCP firewall (optional) --------------------------------
            # Inspect tool calls the model produced before anything downstream
            # (or the client) acts on them. A deny replaces the tool output with
            # a refusal note; allows pass through untouched.
            if self.firewall is not None and response.tool_calls:
                async with self._stage("tool_firewall", spans, trace_id):
                    decisions = [
                        self.firewall.inspect_tool_call(
                            call.name,
                            call.arguments,
                            {"tenant": tenant_id, "trace_id": trace_id},
                        )
                        for call in response.tool_calls
                    ]
                    result.tool_firewall = [d.model_dump() for d in decisions]
                    for decision in decisions:
                        self.metrics.inc(
                            "aegisgate_tool_firewall_total", {"verdict": decision.verdict}
                        )
                    denied = next((d for d in decisions if d.verdict == "deny"), None)
                    if denied is not None:
                        refusal = (
                            f"[aegisgate] Tool call '{denied.tool_name}' was denied by the "
                            f"agent tool firewall.\n{self.firewall.explain(denied)}"
                        )
                        response = response.model_copy(
                            update={"content": refusal, "tool_calls": None}
                        )
                        result.response = response
                        self._log_record(
                            logs,
                            "tool_firewall",
                            "tool call denied",
                            tool=denied.tool_name,
                            risk=denied.risk,
                        )
                    else:
                        self._log_record(
                            logs, "tool_firewall",
                            "tool calls inspected", count=len(decisions),
                        )

            # 7. shadow mode ---------------------------------------------------------------
            async with self._stage("shadow", spans, trace_id):
                await self._mirror_shadow(request, outcome.served_by, response, logs)

            # 8. meter ---------------------------------------------------------------------
            async with self._stage("meter", spans, trace_id):
                cost = self.meter.record_usage(
                    tenant_id,
                    outcome.served_by,
                    response.usage.prompt_tokens,
                    response.usage.completion_tokens,
                )
                result.cost_usd = cost
                self.metrics.inc(
                    "aegisgate_tokens_total",
                    self._tags(tenant_id, outcome.served_by),
                    response.usage.total_tokens,
                )
                self.metrics.gauge("aegisgate_cost_usd", {"tenant": tenant_id}, cost)
            self._log_record(
                logs, "meter", "usage recorded", model=outcome.served_by, cost_usd=round(cost, 6)
            )

            # 9. cache store -----------------------------------------------------------------
            async with self._stage("cache_store", spans, trace_id):
                self.cache.store(request, family, response)

            # 9b. PII restore (optional) -------------------------------------------------------
            # After the cache store: the cache keeps pseudonymized content, the
            # caller's UI gets the original values back.
            if self.pii is not None and pii_map:
                async with self._stage("pii_restore", spans, trace_id):
                    response = response.model_copy(
                        update={"content": self.pii.restore(response.content, pii_map)}
                    )
                    result.response = response
                self._log_record(logs, "pii", "response restored", tokens=len(pii_map))

            self.metrics.inc(
                "aegisgate_requests_total", self._tags(tenant_id, request.model) | {"status": "ok"}
            )
            self.metrics.observe(
                "aegisgate_request_latency_ms",
                {"model": outcome.served_by},
                response.latency_ms or 0.0,
            )
        except PipelineError as exc:
            result.error = exc
            self.metrics.inc(
                "aegisgate_requests_total",
                self._tags(tenant_id, request.model) | {"status": "error"},
            )
        except Exception as exc:  # noqa: BLE001
            result.error = PipelineError(500, "internal_error", str(exc))
            self.metrics.inc(
                "aegisgate_requests_total",
                self._tags(tenant_id, request.model) | {"status": "error"},
            )

        result.spans, result.logs = spans, logs
        return result

    # -- streaming -----------------------------------------------------------------
    async def stream(
        self, request: ChatRequest, tenant_id: str, *, premium: bool = False
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming variant. Preflight stages run up front; execution streams
        from the first available candidate. Hedging and retry are intentionally
        disabled for streams (partial outputs cannot be safely replayed), and
        the tool-firewall auto-inspect plus PII round-trip currently apply to
        the non-streaming path only — a documented trade-off in the README."""
        trace_id = uuid4().hex[:16]
        spans: list[dict[str, Any]] = []
        logs: list[dict[str, Any]] = []
        start = perf_counter()

        def span(name: str, status: str, attrs: dict[str, Any] | None = None) -> None:
            self._emit_span(spans, trace_id, name, status, (perf_counter() - start) * 1000, attrs)

        decision = self.limiter.check(tenant_id, request.model)
        if not decision.allowed:
            span("rate_limit", "error", {"code": "rate_limited"})
            self.metrics.inc("aegisgate_rate_limited_total")
            yield {
                "event": "error",
                "status": 429,
                "code": "rate_limited",
                "message": f"rate limit exceeded for {decision.key}",
                "retry_after_s": decision.retry_after_s,
                "trace_id": trace_id,
            }
            return
        span("rate_limit", "ok", {"tenant": tenant_id})

        family = self.registry.get(request.model).family
        hit = self.cache.lookup(request, family)
        if hit is not None:
            span("cache_lookup", "ok", {"tier": hit.tier})
            self.metrics.inc("aegisgate_cache_hits_total", {"tier": hit.tier})
            self.metrics.inc(
                "aegisgate_requests_total",
                self._tags(tenant_id, request.model) | {"status": "ok"},
            )
            yield {
                "event": "meta",
                "served_by": f"cache:{hit.tier}",
                "model": hit.response.model,
                "cache_tier": hit.tier,
                "trace_id": trace_id,
            }
            for word in hit.response.content.split(" "):
                yield {"event": "chunk", "delta": word + " ", "trace_id": trace_id}
            yield {
                "event": "final",
                "usage": hit.response.usage.model_dump(),
                "model": hit.response.model,
                "trace_id": trace_id,
            }
            return
        span("cache_lookup", "ok", {"hit": False})

        adjustment = self.autopilot.adjust(
            tenant_id, request.messages, premium=premium, has_tools=bool(request.tools)
        )
        if not adjustment.allow:
            span("autopilot", "error", {"code": "budget_exceeded"})
            yield {
                "event": "error",
                "status": 429,
                "code": "budget_exceeded",
                "message": adjustment.reason,
                "trace_id": trace_id,
            }
            return
        span("autopilot", "ok", {"profile": adjustment.profile_hint})

        candidates = self._candidates(request, adjustment, premium=premium)
        scored = self.router.route(candidates, RequestProfile(adjustment.profile_hint))

        served_by = ""
        usage: Usage | None = None
        content_parts: list[str] = []
        for candidate in (s.model for s in scored):
            if not self._availability()(candidate):
                continue
            spec = self.registry.get(candidate)
            try:
                agen = self.client.stream(request.model_copy(update={"model": candidate}))
                first = await agen.__anext__()
            except Exception as exc:  # noqa: BLE001 - try next candidate
                self.breakers.record_failure(spec.provider)
                self.monitor.record(spec.provider, ok=False)
                self._log_record(
                    logs, "stream", "stream candidate failed", model=candidate, error=str(exc)
                )
                continue
            served_by = candidate
            self.breakers.record_success(spec.provider)
            self.monitor.record(spec.provider, ok=True)
            span("execute", "ok", {"served_by": served_by})
            yield {
                "event": "meta",
                "served_by": served_by,
                "model": candidate,
                "trace_id": trace_id,
            }
            content_parts = [first.delta_content]
            if first.delta_content:
                yield {"event": "chunk", "delta": first.delta_content, "trace_id": trace_id}
            async for chunk in agen:
                if chunk.delta_content:
                    content_parts.append(chunk.delta_content)
                    yield {"event": "chunk", "delta": chunk.delta_content, "trace_id": trace_id}
                if chunk.finish_reason:
                    usage = chunk.usage

            content = "".join(content_parts)
            prompt_tokens = usage.prompt_tokens if usage else max(1, len(content) // 4)
            completion_tokens = usage.completion_tokens if usage else max(1, len(content) // 4)
            cost = self.meter.record_usage(tenant_id, served_by, prompt_tokens, completion_tokens)
            self.metrics.inc(
                "aegisgate_requests_total",
                self._tags(tenant_id, request.model) | {"status": "ok"},
            )
            self.metrics.observe(
                "aegisgate_request_latency_ms",
                {"model": served_by},
                (perf_counter() - start) * 1000,
            )
            response = ChatResponse(
                content=content,
                model=served_by,
                provider=spec.provider,
                usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
            )
            self.cache.store(request, family, response)
            yield {
                "event": "final",
                "usage": response.usage.model_dump(),
                "model": served_by,
                "cost_usd": round(cost, 6),
                "trace_id": trace_id,
            }
            return

        span("execute", "error", {"code": "no_model_available"})
        yield {
            "event": "error",
            "status": 503,
            "code": "no_model_available",
            "message": "all candidate models failed",
            "trace_id": trace_id,
        }
