"""P0 hardening: real identity, hashed keys, durable ledger, admin/GDPR API."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aegisgate.auth import JwtProvider, StaticTokenProvider, build_auth_provider
from aegisgate.config import AegisGateSettings
from aegisgate.gateway.app import create_app
from aegisgate.meter_store import SQLiteMeterStore, UsageRecord


# --- identity ---------------------------------------------------------------
def test_jwt_valid_token_resolves_tenant():
    import jwt as pyjwt

    provider = JwtProvider(secret="test-secret", audience="aegisgate")
    token = pyjwt.encode(
        {"sub": "acme", "aud": "aegisgate", "exp": int(__import__("time").time()) + 300},
        "test-secret", algorithm="HS256",
    )
    identity = provider.resolve(token)
    assert identity is not None and identity.tenant_id == "acme"


def test_jwt_expired_token_rejected():
    import jwt as pyjwt

    provider = JwtProvider(secret="test-secret")
    token = pyjwt.encode(
        {"sub": "acme", "exp": int(__import__("time").time()) - 3600},
        "test-secret", algorithm="HS256",
    )
    assert provider.resolve(token) is None


def test_jwt_wrong_signature_and_audience_rejected():
    import jwt as pyjwt

    provider = JwtProvider(secret="test-secret", audience="aegisgate")
    forged = pyjwt.encode({"sub": "acme", "exp": int(__import__("time").time()) + 300},
                          "attacker-secret", algorithm="HS256")
    assert provider.resolve(forged) is None
    wrong_aud = pyjwt.encode({"sub": "acme", "exp": int(__import__("time").time()) + 300,
                              "aud": "elsewhere"}, "test-secret", algorithm="HS256")
    assert provider.resolve(wrong_aud) is None


def test_build_auth_provider_fails_closed_without_secret():
    with pytest.raises(RuntimeError):
        build_auth_provider({"AEGISGATE_AUTH_MODE": "jwt"})


def test_static_keys_hashed_at_rest():
    provider = StaticTokenProvider(raw_pairs="super-secret-token:acme")
    assert "super-secret-token" not in repr(provider)
    assert "super-secret-token" not in str(provider._by_hash)
    assert provider.resolve("super-secret-token").tenant_id == "acme"


def test_static_key_rotation_window():
    provider = StaticTokenProvider(
        raw_pairs="new-token:acme", previous_raw_pairs="old-token:acme"
    )
    assert provider.resolve("new-token").tenant_id == "acme"
    assert provider.resolve("old-token").tenant_id == "acme"  # rotation window
    assert provider.resolve("random") is None


# --- durable ledger ---------------------------------------------------------
def test_ledger_survives_restart():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "ledger.db"
        with SQLiteMeterStore(db) as store:
            store.append_usage(UsageRecord(
                ts=1759100000.0, tenant_id="acme", model="gpt-4o-mini",
                prompt_tokens=100, completion_tokens=50, cost_usd=0.05,
            ))
        # "restart": the old process closed its connection; a new one opens
        with SQLiteMeterStore(db) as store2:
            assert store2.month_spend("acme", "2025-09") == pytest.approx(0.05)


def test_cost_meter_hydrates_from_store():
    from aegisgate.clock import ManualClock

    with tempfile.TemporaryDirectory() as td:
        with SQLiteMeterStore(Path(td) / "ledger.db") as store:
            clock = ManualClock()
            clock.now = lambda: 1759100000.0
            from aegisgate.cost.meter import CostMeter
            from aegisgate.router.registry import ModelRegistry

            registry = ModelRegistry.load(None)  # default bundled registry path
            meter = CostMeter(registry=registry, clock=clock, store=store)
            meter.record_usage("acme", "gpt-4o-mini", 100, 50)
            before = meter.spend("acme", "month")
        # restart: fresh connection over the same ledger
        with SQLiteMeterStore(Path(td) / "ledger.db") as store2:
            meter2 = CostMeter(registry=registry, clock=clock, store=store2)
            assert meter2.spend("acme", "month") == pytest.approx(before)
            assert meter2.spend("acme", "month") > 0, "spend must survive a restart"


# --- admin API: audit trail + GDPR erasure ----------------------------------
@pytest.fixture()
def gateway_client(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGISGATE_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setenv("AEGISGATE_LEDGER_PATH", str(tmp_path / "ledger.db"))
    settings = AegisGateSettings(tenant_tokens="tok1:acme,tok2:globex")
    return TestClient(create_app(settings=settings)), {"Authorization": "Bearer tok1"}


def test_gateway_metering_correlation_and_audit(gateway_client):
    client, headers = gateway_client
    r = client.post("/v1/chat/completions", headers=headers,
                    json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert r.headers.get("x-correlation-id")
    assert r.json()["aegisgate"]["cost_usd"] > 0


def test_gdpr_tenant_deletion_is_audited(gateway_client):
    client, headers = gateway_client
    client.post("/v1/chat/completions", headers=headers,
                json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]})
    r = client.delete("/admin/tenants/acme", headers={"x-admin-token": "admin-secret"})
    assert r.status_code == 200
    assert r.json()["usage_rows_removed"] >= 1
    audit = client.get("/admin/audit", headers={"x-admin-token": "admin-secret"}).json()["entries"]
    actions = [e["action"] for e in audit]
    assert "tenant.delete" in actions, "erasure must itself be audited"


def test_admin_api_requires_token(gateway_client):
    client, _ = gateway_client
    assert client.get("/admin/audit").status_code == 401
    assert client.delete("/admin/tenants/acme").status_code == 401
    assert client.get("/admin/audit", headers={"x-admin-token": "wrong"}).status_code == 401


def test_gateway_rejects_unknown_credentials(gateway_client):
    client, _ = gateway_client
    r = client.post("/v1/chat/completions", headers={"Authorization": "Bearer bogus"},
                    json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 401
