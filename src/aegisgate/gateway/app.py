"""FastAPI service exposing an OpenAI-compatible gateway.

Endpoints:
- POST /v1/chat/completions  (stream + non-stream, Bearer auth -> tenant)
- POST /v1/tools/inspect     (agent tool firewall: {tool_name, arguments, context})
- POST /v1/mcp/inspect       (MCP-shaped firewall: {"tool": {"name"}, "arguments"})
- GET  /v1/models            (registry listing)
- GET  /health
- GET  /metrics              (Prometheus text format)

Tests exercise the app through ASGI (httpx/TestClient), so the full request
lifecycle runs offline with EchoMockClient.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from aegisgate import __version__
from aegisgate.config import AegisGateSettings
from aegisgate.llm.base import ChatRequest, Message, ToolSpec
from aegisgate.llm.echo import EchoMockClient
from aegisgate.llm.openai_compat import OpenAICompatClient
from aegisgate.pipeline import GatewayPipeline, PipelineResult
from aegisgate.router.registry import ModelRegistry

STATE_KEY = "pipeline"


def build_pipeline(
    settings: AegisGateSettings | None = None,
    *,
    clock: Any = None,
    span_sink: Any = None,
    log_sink: Any = None,
) -> GatewayPipeline:
    from aegisgate.clock import SystemClock

    settings = settings or AegisGateSettings()
    clock = clock or SystemClock()  # never pass None into the dataclass default
    registry = ModelRegistry.load(settings.registry_path)
    if settings.llm_mode == "openai":
        client = OpenAICompatClient(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            timeout_s=settings.openai_timeout_s,
        )
    else:
        client = EchoMockClient()
    return GatewayPipeline(
        settings=settings,
        registry=registry,
        client=client,
        clock=clock,
        span_sink=span_sink,
        log_sink=log_sink,
    )


def _tenant_from_headers(request: Request, settings: AegisGateSettings,
                         auth_provider: Any) -> str:
    """Resolve the tenant via the configured AuthProvider (static hashed keys
    or JWT). Kept as a function so tests can drive it directly."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer ") or not auth[7:].strip():
        raise PermissionError("missing bearer token")
    token = auth[7:].strip()
    if not settings.tenant_tokens and auth_provider is None:
        return token  # dev mode: token doubles as tenant id
    identity = auth_provider.resolve(token) if auth_provider else None
    if identity is None:
        raise PermissionError("unknown or expired credentials")
    return identity.tenant_id


def _parse_chat_body(body: dict[str, Any]) -> ChatRequest:
    tools = None
    if body.get("tools"):
        tools = [
            ToolSpec(
                name=t.get("function", {}).get("name", t.get("name", "")),
                parameters=t.get("function", {}).get("parameters", t.get("parameters", {})),
            )
            for t in body["tools"]
        ]
    return ChatRequest(
        model=body["model"],
        messages=[
            Message(role=m.get("role", "user"), content=m.get("content", ""))
            for m in body["messages"]
        ],
        temperature=float(body.get("temperature", 0.3)),
        max_tokens=body.get("max_tokens"),
        tools=tools,
        stream=bool(body.get("stream", False)),
    )


def _completion_payload(result: PipelineResult) -> dict[str, Any]:
    response = result.response
    assert response is not None
    created = int(time.time())
    message: dict[str, Any] = {"role": "assistant", "content": response.content}
    if response.tool_calls and not result.tool_firewall_blocked:
        # OpenAI-shaped passthrough of allowed tool calls
        message["tool_calls"] = [
            {
                "id": call.id or f"call_{index}",
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for index, call in enumerate(response.tool_calls)
        ]
    return {
        "id": f"chatcmpl-{result.trace_id}",
        "object": "chat.completion",
        "created": created,
        "model": response.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": response.finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
        "aegisgate": {
            "tenant": "",
            "served_by": result.served_by,
            "cache_tier": result.cache_tier,
            "hedged": result.hedged,
            "attempts": [a.__dict__ for a in result.attempts],
            "cost_usd": round(result.cost_usd, 6),
            "trace_id": result.trace_id,
            "tool_firewall": result.tool_firewall or [],
            "tool_firewall_blocked": result.tool_firewall_blocked,
        },
    }


def _error_payload(result: PipelineResult) -> dict[str, Any]:
    error = result.error
    assert error is not None
    return {
        "error": {
            "message": error.message,
            "type": error.code,
            "code": error.code,
            "aegisgate_trace_id": result.trace_id,
        }
    }


async def _sse_stream(events: AsyncIterator[dict[str, Any]], model_hint: str) -> AsyncIterator[str]:
    chunk_id = f"chatcmpl-stream-{int(time.time() * 1000)}"
    async for event in events:
        if event["event"] == "meta":
            base = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "model": event.get("model", model_hint),
                "aegisgate": {
                    "served_by": event.get("served_by"),
                    "cache_tier": event.get("cache_tier"),
                },
            }
            first = dict(base)
            first["choices"] = [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]
            yield f"data: {json.dumps(first)}\n\n"
        elif event["event"] == "chunk":
            payload = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "model": event.get("model", model_hint),
                "choices": [
                    {"index": 0, "delta": {"content": event["delta"]}, "finish_reason": None}
                ],
            }
            yield f"data: {json.dumps(payload)}\n\n"
        elif event["event"] == "final":
            payload = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "model": event.get("model", model_hint),
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": event.get("usage", {}),
            }
            yield f"data: {json.dumps(payload)}\n\n"
        elif event["event"] == "error":
            payload = {"error": {"message": event["message"], "code": event["code"]}}
            yield f"data: {json.dumps(payload)}\n\n"
            yield "data: [DONE]\n\n"
            return
    yield "data: [DONE]\n\n"


def create_app(
    settings: AegisGateSettings | None = None,
    pipeline: GatewayPipeline | None = None,
) -> FastAPI:
    import hashlib
    import hmac
    import os
    import uuid

    from aegisgate.auth import StaticTokenProvider, build_auth_provider
    from aegisgate.meter_store import SQLiteMeterStore

    settings = settings or AegisGateSettings()
    owned_pipeline = pipeline is None
    pipeline = pipeline or build_pipeline(settings)

    if os.environ.get("AEGISGATE_AUTH_MODE", "static") == "jwt":
        auth_provider = build_auth_provider()  # JWT mode is env-configured by design
    else:
        auth_provider = StaticTokenProvider(
            raw_pairs=settings.tenant_tokens,
            previous_raw_pairs=os.environ.get("AEGISGATE_TENANT_TOKENS_PREVIOUS", ""),
        )

    # durable ledger: opt-in via AEGISGATE_LEDGER_PATH; when unset the meter
    # stays in-memory (single-process deployments)
    ledger_path = os.environ.get("AEGISGATE_LEDGER_PATH", "")
    if ledger_path and pipeline.meter is not None:
        pipeline.meter.store = SQLiteMeterStore(ledger_path)
        if pipeline.meter.clock is not None:  # clock may be unset when a pipeline is injected
            pipeline.meter._hydrate_from_store()

    admin_secret = os.environ.get("AEGISGATE_ADMIN_TOKEN", "")
    admin_token_hash = hashlib.sha256(admin_secret.encode()).hexdigest()

    def _require_admin(request: Request) -> JSONResponse | None:
        if not admin_secret:
            return JSONResponse(
                status_code=503,
                content={"error": "admin API disabled (set AEGISGATE_ADMIN_TOKEN)"},
            )
        supplied = hashlib.sha256(
            request.headers.get("x-admin-token", "").encode()
        ).hexdigest()
        if not hmac.compare_digest(supplied, admin_token_hash):
            return JSONResponse(status_code=401, content={"error": "invalid admin token"})
        return None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pipeline = pipeline
        yield
        aclose = getattr(pipeline.client, "aclose", None)
        if aclose is not None:
            await aclose()

    app = FastAPI(title="AegisGate", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def correlation_id(request: Request, call_next):
        cid = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        request.state.correlation_id = cid
        import logging as _logging

        _logging.getLogger("aegisgate.access").info(
            json.dumps({"correlation_id": cid, "method": request.method,
                        "path": request.url.path, "event": "request"}),
        )
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        return response

    @app.get("/admin/audit")
    async def admin_audit(request: Request, limit: int = 50):
        denied = _require_admin(request)
        if denied:
            return denied
        meter = getattr(pipeline, "meter", None)
        has_store = meter is not None and getattr(meter, "store", None) is not None
        tail = meter.store.audit_tail(limit) if has_store else []
        return {"entries": tail}

    @app.delete("/admin/tenants/{tenant_id}")
    async def admin_delete_tenant(tenant_id: str, request: Request):
        denied = _require_admin(request)
        if denied:
            return denied
        meter = getattr(pipeline, "meter", None)
        if meter is None:
            return JSONResponse(status_code=503, content={"error": "no meter attached"})
        removed = meter.delete_tenant(tenant_id)  # audited inside
        return {"tenant_id": tenant_id, "usage_rows_removed": removed}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        try:
            tenant = _tenant_from_headers(request, settings, auth_provider)
        except PermissionError as exc:
            return JSONResponse(
                status_code=401,
                content={"error": {"message": str(exc), "type": "invalid_api_key"}},
            )
        body = await request.json()
        try:
            chat_request = _parse_chat_body(body)
        except (KeyError, ValueError) as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": f"invalid request body: {exc}",
                        "type": "invalid_request_error",
                    }
                },
            )
        premium = request.headers.get("x-aegisgate-premium", "").lower() in {"1", "true"} or bool(
            body.get("x_aegisgate_premium", False)
        )

        if chat_request.stream:
            events = pipeline.stream(chat_request, tenant, premium=premium)
            return StreamingResponse(
                _sse_stream(events, chat_request.model), media_type="text/event-stream"
            )

        result = await pipeline.handle(chat_request, tenant, premium=premium)
        if result.error is not None:
            headers = {}
            if result.error.retry_after_s:
                headers["Retry-After"] = str(max(1, int(result.error.retry_after_s + 0.999)))
            return JSONResponse(
                status_code=result.error.status_code,
                content=_error_payload(result),
                headers=headers,
            )
        payload = _completion_payload(result)
        payload["aegisgate"]["tenant"] = tenant
        return JSONResponse(status_code=200, content=payload)

    @app.post("/v1/tools/inspect")
    async def tools_inspect(request: Request) -> Any:
        """Inspect one model-issued tool call: body {tool_name, arguments, context}."""
        denied = await _run_inspect(request, mcp_shape=False)
        return denied

    @app.post("/v1/mcp/inspect")
    async def mcp_inspect(request: Request) -> Any:
        """MCP-shaped inspect: body {"tool": {"name": ...}, "arguments": {...}}."""
        denied = await _run_inspect(request, mcp_shape=True)
        return denied

    async def _run_inspect(request: Request, *, mcp_shape: bool) -> Any:
        try:
            _tenant_from_headers(request, settings, auth_provider)
        except PermissionError as exc:
            return JSONResponse(
                status_code=401,
                content={"error": {"message": str(exc), "type": "invalid_api_key"}},
            )
        if pipeline.firewall is None:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "message": "tools firewall disabled "
                        "(set AEGISGATE_TOOLS_FIREWALL_ENABLED=true)",
                        "type": "firewall_disabled",
                    }
                },
            )
        body = await request.json()
        if mcp_shape:
            tool = body.get("tool") or {}
            tool_name = tool.get("name", "") if isinstance(tool, dict) else str(tool)
        else:
            # accept the shapes a developer naturally reaches for first:
            # {"tool": "bash"} and {"tool_name": "bash"} and {"tool": {"name": ...}}
            raw_tool = body.get("tool_name") or body.get("tool") or ""
            tool_name = (
                raw_tool.get("name", "") if isinstance(raw_tool, dict) else str(raw_tool)
            )
        arguments = body.get("arguments") or body.get("args") or {}
        if not tool_name:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {"message": "missing tool name", "type": "invalid_request_error"}
                },
            )
        decision = pipeline.firewall.inspect_tool_call(tool_name, arguments, body.get("context"))
        return JSONResponse(status_code=200, content=decision.model_dump())

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": spec.id,
                    "object": "model",
                    "owned_by": spec.provider,
                    "aegisgate": {
                        "quality_tier": spec.quality_tier,
                        "context_window": spec.context_window,
                        "capabilities": spec.capabilities,
                        "price_per_1m_input": spec.price_per_1m_input,
                        "price_per_1m_output": spec.price_per_1m_output,
                    },
                }
                for spec in pipeline.registry.models
            ],
        }

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "quarantined_providers": [
                provider
                for provider, snap in pipeline.monitor.snapshot().items()
                if snap["quarantined"]
            ],
        }

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        state_rank = {"closed": 0, "half_open": 1, "open": 2}
        for provider, snap in pipeline.breakers.snapshot().items():
            pipeline.metrics.gauge(
                "aegisgate_breaker_state", {"provider": provider}, float(state_rank[snap["state"]])
            )
        for tenant in _metered_tenants(pipeline):
            status = pipeline.meter.status(tenant)
            pipeline.metrics.gauge(
                "aegisgate_monthly_spend_usd", {"tenant": tenant}, status.monthly_spend
            )
        return PlainTextResponse(
            pipeline.metrics.render(), media_type="text/plain; charset=utf-8"
        )

    app.state.settings = settings
    app.state.owned_pipeline = owned_pipeline
    return app


def _metered_tenants(pipeline: GatewayPipeline) -> list[str]:
    monthly = getattr(pipeline.meter, "_monthly", {})
    return sorted(monthly.keys())


if __name__ == "__main__":  # pragma: no cover - manual run entry point
    import uvicorn

    run_settings = AegisGateSettings()
    uvicorn.run(create_app(run_settings), host=run_settings.host, port=run_settings.port)
