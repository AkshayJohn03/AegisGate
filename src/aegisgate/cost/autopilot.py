"""Cost autopilot: a policy engine that turns budget pressure into routing
constraints, plus a heuristic task-complexity classifier.

Policy rules (evaluated per request, per tenant):
- hard budget reached           -> block (allow=False)
- easy task, non-premium        -> cap output price (route to cheap models)
- projected month-end burn >= threshold of monthly hard budget and the
  request is not premium-flagged -> downgrade routing by one quality tier
  (exclude the most expensive tier).

On "downgrade": the brief said "raise the minimum quality_tier requirement".
With tier 5 = frontier (the registry convention, and the scoring formula
penalizes low tiers), raising a *minimum* tier under budget pressure would
route to *more* expensive models and accelerate burn. We implement the
semantic intent — degrade routing by one tier — as a max-tier ceiling drop,
and document the trade-off in the README.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel

from aegisgate.cost.meter import BudgetLevel, CostMeter
from aegisgate.llm.base import Message
from aegisgate.router.registry import ModelSpec


class Complexity(StrEnum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class AutopilotConfig(BaseModel):
    projected_burn_threshold: float = 0.8
    downgrade_steps: int = 1
    easy_output_price_cap: float = 2.0
    max_tier_ceiling: int = 5


class RoutingAdjustment(BaseModel):
    allow: bool = True
    reason: str = ""
    complexity: Complexity = Complexity.medium
    min_quality_tier: int = 1
    max_quality_tier: int = 5
    max_output_price: float | None = None
    profile_hint: str = "balanced"

    def admits(self, spec: ModelSpec) -> bool:
        if not spec.enabled:
            return False
        if not (self.min_quality_tier <= spec.quality_tier <= self.max_quality_tier):
            return False
        if self.max_output_price is not None and spec.price_per_1m_output > self.max_output_price:
            return False
        return True


class ComplexityClassifier:
    """Heuristic task complexity: prompt size, code fences, structured-output
    demand. Cheap, deterministic, and good enough to steer cheap traffic to
    cheap models; a trained classifier can slot in behind the same API."""

    CODE_FENCE = re.compile(r"```")
    STRUCTURED = re.compile(r"\b(json|schema|structured|csv|yaml|regex)\b", re.IGNORECASE)

    def classify(self, messages: list[Message], *, has_tools: bool = False) -> Complexity:
        text = " ".join(m.content for m in messages)
        chars = len(text)
        fences = len(self.CODE_FENCE.findall(text)) // 2
        structured = bool(self.STRUCTURED.search(text)) or has_tools
        if (fences > 0 or structured) and chars > 1200:
            return Complexity.hard
        if fences > 1 or (structured and chars > 400):
            return Complexity.hard
        if chars < 500 and not structured:
            return Complexity.easy
        return Complexity.medium


_COMPLEXITY_PROFILE = {
    Complexity.easy: "cost_sensitive",
    Complexity.medium: "balanced",
    Complexity.hard: "quality_sensitive",
}


class CostAutopilot:
    def __init__(
        self,
        meter: CostMeter,
        classifier: ComplexityClassifier | None = None,
        config: AutopilotConfig | None = None,
    ) -> None:
        self.meter = meter
        self.classifier = classifier or ComplexityClassifier()
        self.config = config or AutopilotConfig()

    def adjust(
        self,
        tenant_id: str,
        messages: list[Message],
        *,
        premium: bool = False,
        has_tools: bool = False,
    ) -> RoutingAdjustment:
        status = self.meter.status(tenant_id)
        budget = self.meter.budget_for(tenant_id)
        complexity = self.classifier.classify(messages, has_tools=has_tools)
        adjustment = RoutingAdjustment(
            complexity=complexity, profile_hint=_COMPLEXITY_PROFILE[complexity]
        )

        if status.level is BudgetLevel.hard and not premium:
            adjustment.allow = False
            adjustment.reason = "hard budget reached; traffic blocked"
            return adjustment

        reasons: list[str] = []
        if complexity is Complexity.easy and not premium:
            adjustment.max_output_price = self.config.easy_output_price_cap
            reasons.append(
                f"easy task: output price capped at ${self.config.easy_output_price_cap}/1M"
            )

        burn_ratio = (
            status.projected_month_end / budget.monthly_hard if budget.monthly_hard > 0 else 0.0
        )
        if burn_ratio >= self.config.projected_burn_threshold and not premium:
            adjustment.max_quality_tier = max(
                1, self.config.max_tier_ceiling - self.config.downgrade_steps
            )
            reasons.append(
                f"projected month-end burn ${status.projected_month_end:.2f} "
                f">= {self.config.projected_burn_threshold:.0%} of hard budget: "
                f"max tier downgraded to {adjustment.max_quality_tier}"
            )
        if status.level is BudgetLevel.soft:
            reasons.append("soft budget warning")

        adjustment.reason = "; ".join(reasons) or "no adjustment"
        return adjustment
