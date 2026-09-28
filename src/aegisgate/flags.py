"""Feature flags: deterministic rollouts, sticky A/B, kill switches, shadow.

- Rollout: sha256(flag + ":" + subject) bucketed into 10,000 buckets. A given
  subject is in or out permanently — across restarts, processes, and replicas
  — because assignment is pure hashing, not stored state.
- A/B: same trick over the session id, so a session is sticky to a variant.
- Kill switch: per-model boolean; the pipeline drops killed models from
  routing (the self-heal monitor sets these on quarantine).
- Shadow mode: mirror a request to a candidate model, record the diff,
  never serve the shadow output to the user.
- State persists to JSON (atomic tmp+rename) so ops changes survive restarts.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel


def _stable_hash(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


class ShadowConfig(BaseModel):
    enabled: bool = False
    candidate: str = ""


@dataclass
class ShadowDiff:
    model: str
    candidate: str
    prompt_preview: str
    primary_output: str
    candidate_output: str
    ts: float


@dataclass
class FlagsState:
    rollouts: dict[str, float] = field(default_factory=dict)
    kill_switches: dict[str, bool] = field(default_factory=dict)
    shadow: dict[str, ShadowConfig] = field(default_factory=dict)
    ab: dict[str, list[str]] = field(default_factory=dict)


class FeatureFlags:
    def __init__(self, store_path: str | Path | None = None) -> None:
        self.store_path = Path(store_path) if store_path else None
        self.state = FlagsState()
        self.shadow_diffs: list[ShadowDiff] = []
        self.load()

    # -- deterministic rollout ------------------------------------------------
    def set_rollout(self, flag: str, percent: float) -> None:
        self.state.rollouts[flag] = float(percent)
        self.save()

    def rollout_percent(self, flag: str, default: float = 0.0) -> float:
        return self.state.rollouts.get(flag, default)

    def is_enabled(self, flag: str, subject: str, default_percent: float = 0.0) -> bool:
        percent = self.rollout_percent(flag, default_percent)
        bucket = _stable_hash(f"{flag}:{subject}") % 10_000
        return bucket < percent * 100

    # -- sticky A/B -----------------------------------------------------------
    def set_ab_variants(self, flag: str, variants: list[str]) -> None:
        self.state.ab[flag] = list(variants)
        self.save()

    def ab_variant(self, flag: str, session_id: str, variants: list[str] | None = None) -> str:
        options = variants or self.state.ab.get(flag) or ["control"]
        index = _stable_hash(f"{flag}:ab:{session_id}") % len(options)
        return options[index]

    # -- kill switch ----------------------------------------------------------
    def set_kill_switch(self, model: str, on: bool = True) -> None:
        self.state.kill_switches[model] = bool(on)
        self.save()

    def is_killed(self, model: str) -> bool:
        return bool(self.state.kill_switches.get(model, False))

    def is_model_available(self, model: str) -> bool:
        return not self.is_killed(model)

    # -- shadow mode ----------------------------------------------------------
    def set_shadow(self, model: str, candidate: str, enabled: bool = True) -> None:
        self.state.shadow[model] = ShadowConfig(enabled=enabled, candidate=candidate)
        self.save()

    def shadow_target(self, model: str) -> str | None:
        config = self.state.shadow.get(model)
        if config and config.enabled and config.candidate:
            return config.candidate
        return None

    def record_shadow_diff(
        self, model: str, candidate: str, prompt: str, primary_output: str, candidate_output: str
    ) -> ShadowDiff:
        diff = ShadowDiff(
            model=model,
            candidate=candidate,
            prompt_preview=prompt[:200],
            primary_output=primary_output,
            candidate_output=candidate_output,
            ts=time.time(),
        )
        self.shadow_diffs.append(diff)
        return diff

    # -- persistence ----------------------------------------------------------
    def save(self) -> None:
        if self.store_path is None:
            return
        payload = {
            "rollouts": self.state.rollouts,
            "kill_switches": self.state.kill_switches,
            "shadow": {k: v.model_dump() for k, v in self.state.shadow.items()},
            "ab": self.state.ab,
        }
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store_path.with_suffix(self.store_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.store_path)

    def load(self) -> None:
        if self.store_path is None or not self.store_path.exists():
            return
        raw = json.loads(self.store_path.read_text(encoding="utf-8"))
        self.state.rollouts = dict(raw.get("rollouts", {}))
        self.state.kill_switches = dict(raw.get("kill_switches", {}))
        self.state.shadow = {
            k: ShadowConfig(**v) for k, v in raw.get("shadow", {}).items()
        }
        self.state.ab = dict(raw.get("ab", {}))
