"""Pipeline orchestration: stages, spans, caching, rerouting, rate limits."""

from __future__ import annotations

import asyncio

from aegisgate.clock import ManualClock
from aegisgate.config import AegisGateSettings
from aegisgate.llm.echo import EchoMockClient
from aegisgate.pipeline import GatewayPipeline
from aegisgate.router.registry import ModelRegistry

REQUIRED_STAGES = [
    "rate_limit",
    "flags",
    "cache_lookup",
    "autopilot",
    "route",
    "execute",
    "meter",
    "cache_store",
]


def build_pipeline(**overrides) -> tuple[GatewayPipeline, ManualClock]:
    clock = ManualClock()
    settings = AegisGateSettings(**overrides)
    pipeline = GatewayPipeline(
        settings=settings,
        registry=ModelRegistry.load(),
        client=EchoMockClient(),
        clock=clock,
    )
    return pipeline, clock


def test_full_lifecycle_emits_all_stages_and_spans(make_request):
    emitted: list[dict] = []
    pipeline, _ = build_pipeline()
    pipeline.span_sink = emitted.append
    request = make_request(content="write me a haiku", model="gpt-4o")

    result = asyncio.run(pipeline.handle(request, "acme"))

    assert result.ok
    stage_names = [s["name"] for s in result.spans]
    for stage in REQUIRED_STAGES:
        assert stage in stage_names, stage
    # ForensiQ-compatible span shape
    for span in result.spans:
        assert set(span) == {
            "span_id",
            "parent_id",
            "name",
            "stage",
            "duration_ms",
            "status",
            "attrs",
        }
        assert span["parent_id"] == result.trace_id
        assert span["status"] == "ok"
        assert span["duration_ms"] >= 0
    assert len(emitted) == len(result.spans)  # sink received every span
    assert result.served_by != ""
    assert result.cost_usd > 0
    assert result.attempts and result.attempts[0].status == "ok"


def test_exact_cache_hit_on_second_identical_request(make_request):
    pipeline, _ = build_pipeline()
    request = make_request(content="deterministic please", model="gpt-4o")

    first = asyncio.run(pipeline.handle(request, "acme"))
    second = asyncio.run(pipeline.handle(request, "acme"))

    assert first.cache_tier is None
    assert second.cache_tier == "exact"
    assert second.served_by == "cache:exact"
    assert second.cost_usd == 0.0  # no metering on cache hits


def test_rate_limit_returns_429_with_retry_after(make_request):
    pipeline, clock = build_pipeline(rate_limit_capacity=1.0, rate_limit_refill_per_sec=0.000001)
    request = make_request(content="hi", model="gpt-4o")
    assert asyncio.run(pipeline.handle(request, "acme")).ok
    blocked = asyncio.run(pipeline.handle(request, "acme"))
    assert blocked.error is not None
    assert blocked.error.status_code == 429
    assert blocked.error.code == "rate_limited"
    assert blocked.error.retry_after_s > 0
    del clock


def test_kill_switch_reroutes_away_from_killed_model(make_request):
    pipeline, _ = build_pipeline()
    pipeline.flags.set_kill_switch("gpt-4o", True)
    # long prompt -> medium complexity -> no easy-task price cap, gpt-4o would
    # be a legitimate candidate if it were not kill-switched
    request = make_request(content="explain this: " + "x" * 600, model="gpt-4o")
    result = asyncio.run(pipeline.handle(request, "acme"))
    assert result.ok
    assert result.served_by != "gpt-4o"
    assert "cache" not in result.served_by


def test_all_models_killed_returns_503(make_request):
    pipeline, _ = build_pipeline()
    for model in pipeline.registry.ids():
        pipeline.flags.set_kill_switch(model, True)
    result = asyncio.run(pipeline.handle(make_request(content="hi"), "acme"))
    assert result.error is not None
    assert result.error.status_code == 503


def test_hard_budget_blocks_request(make_request):
    pipeline, _ = build_pipeline(daily_hard=0.000001)
    request = make_request(content="expensive things", model="gpt-4o")
    first = asyncio.run(pipeline.handle(request, "acme"))
    assert first.ok  # serves, and the metering pushes daily spend past the limit
    # different content so the cache cannot short-circuit the budget check
    second_request = make_request(content="more spend", model="gpt-4o")
    second = asyncio.run(pipeline.handle(second_request, "acme"))
    assert second.error is not None
    assert second.error.code == "budget_exceeded"


def test_streaming_lifecycle_emits_chunks_and_usage(make_request):
    pipeline, _ = build_pipeline()
    request = make_request(content="stream this", model="gpt-4o")
    events = asyncio.run(collect(pipeline.stream(request, "acme")))
    kinds = [e["event"] for e in events]
    assert kinds[0] == "meta"
    assert "chunk" in kinds
    assert kinds[-1] == "final"
    final = events[-1]
    assert final["usage"]["completion_tokens"] > 0
    content = "".join(e.get("delta", "") for e in events if e["event"] == "chunk")
    assert "stream this" in content


async def collect(agen):
    return [event async for event in agen]


def test_metrics_counters_populated(make_request):
    pipeline, _ = build_pipeline()
    asyncio.run(pipeline.handle(make_request(content="count me"), "acme"))
    snapshot = pipeline.metrics.snapshot()
    assert any(key.startswith("aegisgate_requests_total") for key in snapshot["counters"])
    assert snapshot["histogram_series"] >= 1
