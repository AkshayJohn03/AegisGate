"""PII round-trip tests: detection, cross-call token consistency, restore
fidelity, LRU bounds, and the optional anonymize pipeline stage over ASGI."""

from __future__ import annotations

from fastapi.testclient import TestClient

from aegisgate.clock import ManualClock
from aegisgate.config import AegisGateSettings
from aegisgate.gateway.app import build_pipeline, create_app
from aegisgate.pii import InMemoryPseudonymStore, PIIConfig, PIIRoundTrip

AUTH = {"Authorization": "Bearer sk-demo", "Content-Type": "application/json"}


def test_email_detection_and_cross_call_token_consistency():
    rt = PIIRoundTrip()
    first, map1 = rt.pseudonymize("contact alice@example.com for details")
    assert first == "contact Email_1 for details"
    assert map1 == {"Email_1": "alice@example.com"}

    # same original -> same token on a later call (the round-trip store)
    second, map2 = rt.pseudonymize("again alice@example.com and bob@example.org")
    assert "Email_1" in second
    assert "Email_2" in second
    assert map2["Email_2"] == "bob@example.org"


def test_phone_detection():
    rt = PIIRoundTrip()
    sanitized, mapping = rt.pseudonymize("call 415-555-2671 or +1 212 555 0199 today")
    assert "Phone_1" in sanitized
    assert "Phone_2" in sanitized
    assert set(mapping.values()) == {"415-555-2671", "+1 212 555 0199"}


def test_credit_card_luhn_valid_detected_invalid_rejected():
    rt = PIIRoundTrip()
    sanitized, mapping = rt.pseudonymize("card 4111 1111 1111 1111 on file")
    assert "4111" not in sanitized
    assert mapping == {"Card_1": "4111 1111 1111 1111"}

    # fails the Luhn checksum -> not PII
    keep, _ = rt.pseudonymize("card 4111 1111 1111 1112 on file")
    assert "4111 1111 1111 1112" in keep


def test_name_heuristics_honorific_and_known_list():
    rt = PIIRoundTrip(config=PIIConfig(known_names=["Ramesh Patel"]))
    sanitized, mapping = rt.pseudonymize("Dr. Alice Smith met Ramesh Patel at 10am")
    assert "User_1" in sanitized
    assert "User_2" in sanitized
    assert set(mapping.values()) == {"Alice Smith", "Ramesh Patel"}


def test_restore_fidelity_round_trip():
    rt = PIIRoundTrip()
    original = (
        "Email alice@example.com or call 415-555-2671 about card 4111 1111 1111 1111. "
        "Dr. Bob Lee approved it."
    )
    sanitized, mapping = rt.pseudonymize(original)
    assert sanitized != original
    assert "alice@example.com" not in sanitized
    assert rt.restore(sanitized, mapping) == original


def test_restore_via_store_without_explicit_map():
    rt = PIIRoundTrip()
    sanitized, _ = rt.pseudonymize("ping dana@example.net twice")
    # a later call can restore using the store alone (cache-hit path)
    assert rt.restore(sanitized) == "ping dana@example.net twice"


def test_unknown_tokens_are_left_untouched():
    rt = PIIRoundTrip()
    text = "User_999 and Email_42 are not mine"
    assert rt.restore(text) == text


def test_store_lru_eviction_bounds_memory():
    store = InMemoryPseudonymStore(max_entries=4)
    rt = PIIRoundTrip(config=PIIConfig(store_max_entries=4), store=store)
    tokens = []
    for i in range(5):
        token, _ = rt.pseudonymize(f"user{i}@example.com")
        tokens.append(token.strip())
    assert store.get_original(tokens[0]) is None  # evicted (LRU)
    assert store.get_original(tokens[-1]) == "user4@example.com"


def test_detection_disabled_by_config():
    rt = PIIRoundTrip(config=PIIConfig(detect_emails=False))
    sanitized, mapping = rt.pseudonymize("mail me at a@b.com")
    assert sanitized == "mail me at a@b.com"
    assert mapping == {}


# -- pipeline / gateway integration (pii_mode=anonymize) -----------------------


def make_pii_app() -> TestClient:
    settings = AegisGateSettings(tenant_tokens="sk-demo:demo", pii_mode="anonymize")
    pipeline = build_pipeline(settings, clock=ManualClock())
    return TestClient(create_app(settings, pipeline)), pipeline


def test_pipeline_anonymizes_request_and_restores_response():
    client, pipeline = make_pii_app()
    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": "Email alice@example.com to book the demo"}
            ],
        },
    )
    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"]
    # the caller's UI gets the original value back
    assert "alice@example.com" in content
    # ...but the provider only ever saw the pseudonym
    seen = pipeline.client.calls[0].messages[-1].content
    assert "alice@example.com" not in seen
    assert "Email_1" in seen


def test_pii_off_by_default_leaves_content_untouched():
    settings = AegisGateSettings(tenant_tokens="sk-demo:demo")
    pipeline = build_pipeline(settings, clock=ManualClock())
    client = TestClient(create_app(settings, pipeline))
    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Email alice@example.com"}],
        },
    )
    content = response.json()["choices"][0]["message"]["content"]
    assert "alice@example.com" in content
    assert pipeline.client.calls[0].messages[-1].content == "Email alice@example.com"
