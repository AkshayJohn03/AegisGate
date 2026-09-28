"""Gateway ASGI tests: full request lifecycle over httpx/TestClient — no
network, EchoMockClient, manual clock."""

from __future__ import annotations

from fastapi.testclient import TestClient

from aegisgate.clock import ManualClock
from aegisgate.config import AegisGateSettings
from aegisgate.gateway.app import build_pipeline, create_app


def make_app(**settings_overrides) -> TestClient:
    settings = AegisGateSettings(tenant_tokens="sk-demo:demo", **settings_overrides)
    pipeline = build_pipeline(settings, clock=ManualClock())
    return TestClient(create_app(settings, pipeline))


AUTH = {"Authorization": "Bearer sk-demo"}


def test_chat_completions_full_lifecycle():
    client = make_app()
    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "say hi"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert "say hi" in body["choices"][0]["message"]["content"]
    assert body["usage"]["total_tokens"] > 0
    aegis = body["aegisgate"]
    assert aegis["tenant"] == "demo"
    assert aegis["served_by"]
    assert aegis["trace_id"]
    assert isinstance(aegis["attempts"], list)
    assert aegis["cost_usd"] >= 0


def test_missing_or_unknown_bearer_token_is_401():
    client = make_app()
    no_auth = client.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": []})
    assert no_auth.status_code == 401
    assert (
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-wrong"},
            json={"model": "gpt-4o", "messages": []},
        ).status_code
        == 401
    )


def test_streaming_endpoint_emits_sse():
    client = make_app()
    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "stream me"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    text = response.text
    assert text.count("data: ") >= 3
    assert "chat.completion.chunk" in text
    assert text.rstrip().endswith("data: [DONE]")
    assert '"finish_reason": "stop"' in text or '"finish_reason":"stop"' in text


def test_models_endpoint_lists_registry():
    client = make_app()
    response = client.get("/v1/models")
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 12
    gpt4o = next(m for m in data if m["id"] == "gpt-4o")
    assert gpt4o["owned_by"] == "openai"
    assert gpt4o["aegisgate"]["quality_tier"] == 5


def test_health_endpoint():
    client = make_app()
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["quarantined_providers"] == []


def test_metrics_endpoint_prometheus_format():
    client = make_app()
    client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "count me"}]},
    )
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    assert "aegisgate_requests_total" in text
    assert 'tenant="demo"' in text
    assert "aegisgate_request_latency_ms_bucket" in text
    assert "aegisgate_cache_misses_total" in text


def test_rate_limit_returns_429_with_retry_after_header():
    client = make_app(rate_limit_capacity=1.0, rate_limit_refill_per_sec=0.000001)
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "one"}]}
    assert client.post("/v1/chat/completions", headers=AUTH, json=body).status_code == 200
    second = client.post("/v1/chat/completions", headers=AUTH, json=body)
    assert second.status_code == 429
    assert "retry-after" in {k.lower() for k in second.headers}
    assert second.json()["error"]["code"] == "rate_limited"


def test_second_identical_request_served_from_cache():
    client = make_app()
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "cache me"}]}
    first = client.post("/v1/chat/completions", headers=AUTH, json=body).json()
    second = client.post("/v1/chat/completions", headers=AUTH, json=body).json()
    assert first["aegisgate"]["cache_tier"] is None
    assert second["aegisgate"]["cache_tier"] == "exact"
    assert second["aegisgate"]["served_by"] == "cache:exact"
    assert second["aegisgate"]["cost_usd"] == 0.0


def test_invalid_body_is_400():
    client = make_app()
    response = client.post("/v1/chat/completions", headers=AUTH, json={"model": "gpt-4o"})
    assert response.status_code == 400
