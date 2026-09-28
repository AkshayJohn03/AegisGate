"""Feature flags: deterministic rollouts, sticky A/B, kill switches, shadow."""

from __future__ import annotations

from aegisgate.flags import FeatureFlags


def test_rollout_is_deterministic_for_same_subject():
    flags = FeatureFlags()
    flags.set_rollout("new-router", 50.0)
    first = [flags.is_enabled("new-router", f"user-{i}") for i in range(50)]
    second = [flags.is_enabled("new-router", f"user-{i}") for i in range(50)]
    assert first == second  # stable across evaluations


def test_rollout_bounds():
    flags = FeatureFlags()
    flags.set_rollout("all-off", 0.0)
    flags.set_rollout("all-on", 100.0)
    assert not any(flags.is_enabled("all-off", f"s{i}") for i in range(200))
    assert all(flags.is_enabled("all-on", f"s{i}") for i in range(200))


def test_rollout_roughly_respects_percentage():
    flags = FeatureFlags()
    flags.set_rollout("half", 50.0)
    enabled = sum(flags.is_enabled("half", f"subject-{i}") for i in range(500))
    assert 150 <= enabled <= 350  # loose distribution sanity


def test_sticky_ab_assignment():
    flags = FeatureFlags()
    flags.set_ab_variants("checkout", ["control", "treatment"])
    first = flags.ab_variant("checkout", "session-123")
    for _ in range(5):
        assert flags.ab_variant("checkout", "session-123") == first
    assert first in {"control", "treatment"}


def test_kill_switch_blocks_model():
    flags = FeatureFlags()
    assert flags.is_model_available("gpt-4o")
    flags.set_kill_switch("gpt-4o", True)
    assert flags.is_killed("gpt-4o")
    assert not flags.is_model_available("gpt-4o")
    assert flags.is_model_available("claude-haiku")
    flags.set_kill_switch("gpt-4o", False)
    assert flags.is_model_available("gpt-4o")


def test_shadow_mode_records_diffs_but_has_no_serving_path():
    flags = FeatureFlags()
    flags.set_shadow("gpt-4o", "claude-sonnet-4", enabled=True)
    assert flags.shadow_target("gpt-4o") == "claude-sonnet-4"
    diff = flags.record_shadow_diff(
        model="gpt-4o",
        candidate="claude-sonnet-4",
        prompt="hello",
        primary_output="primary answer",
        candidate_output="candidate answer",
    )
    assert diff in flags.shadow_diffs
    assert diff.primary_output == "primary answer"
    flags.set_shadow("gpt-4o", "claude-sonnet-4", enabled=False)
    assert flags.shadow_target("gpt-4o") is None


def test_json_persistence_roundtrip(tmp_path):
    path = tmp_path / "flags.json"
    flags = FeatureFlags(store_path=path)
    flags.set_rollout("canary", 25.0)
    flags.set_kill_switch("o3-mini", True)
    flags.set_shadow("gpt-4o", "gemini-2.0-flash")
    assert path.exists()

    reloaded = FeatureFlags(store_path=path)
    assert reloaded.rollout_percent("canary") == 25.0
    assert reloaded.is_killed("o3-mini")
    assert reloaded.shadow_target("gpt-4o") == "gemini-2.0-flash"
