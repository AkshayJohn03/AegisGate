"""Per-tenant token and cost accounting with soft/hard budgets.

Usage arrives per request (from streamed or complete responses), is priced
against registry rates, and is bucketed by UTC day and month derived from the
injected Clock. Budget status includes a month-end burn projection:
``spend_so_far / day_of_month * days_in_month`` — the signal the autopilot
acts on.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from aegisgate.clock import Clock
from aegisgate.meter_store import UsageRecord
from aegisgate.router.registry import ModelRegistry


class Budget(BaseModel):
    daily_soft: float = 5.0
    daily_hard: float = 10.0
    monthly_soft: float = 50.0
    monthly_hard: float = 100.0


class BudgetLevel(StrEnum):
    ok = "ok"
    soft = "soft"
    hard = "hard"


class BudgetStatus(BaseModel):
    tenant_id: str
    level: BudgetLevel = BudgetLevel.ok
    daily_spend: float = 0.0
    monthly_spend: float = 0.0
    projected_month_end: float = 0.0
    day_of_month: int = 1
    days_in_month: int = 30
    reasons: list[str] = []


@dataclass
class _Totals:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, prompt: int, completion: int, cost: float) -> None:
        self.requests += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.cost_usd += cost


@dataclass
class CostMeter:
    registry: ModelRegistry
    clock: Clock
    default_budget: Budget = field(default_factory=Budget)
    budgets: dict[str, Budget] = field(default_factory=dict)
    store: Any | None = None  # MeterStore: durable ledger; None = in-memory only
    admin_actor: str = "system"

    def __post_init__(self) -> None:
        # _daily[tenant][day_key][model] = _Totals ; same for months
        self._daily: dict[str, dict[str, dict[str, _Totals]]] = {}
        self._monthly: dict[str, dict[str, dict[str, _Totals]]] = {}
        if self.store is not None:
            self._hydrate_from_store()

    def _hydrate_from_store(self) -> None:
        """Restart safety: rebuild month/day aggregates from the durable
        ledger so budget enforcement survives process hops."""
        now = datetime.fromtimestamp(self.clock.now(), tz=UTC)
        month_key = now.strftime("%Y-%m")
        for tenant in self.store.list_tenants():
            for r in self.store.usage_rows(tenant, month_key):
                day_key = datetime.fromtimestamp(r.ts, tz=UTC).strftime("%Y-%m-%d")
                d = self._daily.setdefault(tenant, {}).setdefault(day_key, {})
                d[r.model] = d.get(r.model) or _Totals()
                d[r.model].add(r.prompt_tokens, r.completion_tokens, r.cost_usd)
                m = self._monthly.setdefault(tenant, {}).setdefault(month_key, {})
                m[r.model] = m.get(r.model) or _Totals()
                m[r.model].add(r.prompt_tokens, r.completion_tokens, r.cost_usd)
        stored = self.store.get_budget("*default*")
        if stored:
            self.default_budget = Budget.model_validate_json(stored)

    def set_budget(self, tenant_id: str, budget: Budget) -> None:
        """Persist (durable store) and audit a budget change."""
        self.budgets[tenant_id] = budget
        if self.store is not None:
            self.store.set_budget(tenant_id, budget.model_dump_json())
            self.store.audit(self.admin_actor, "budget.set", tenant_id,
                             budget.model_dump())

    def delete_tenant(self, tenant_id: str) -> int:
        """GDPR erasure across memory + durable store. Audited."""
        removed = 0
        for table in (self._daily, self._monthly):
            if tenant_id in table:
                del table[tenant_id]
        self.budgets.pop(tenant_id, None)
        if self.store is not None:
            removed = self.store.delete_tenant(tenant_id)
            self.store.audit(self.admin_actor, "tenant.delete", tenant_id,
                             {"usage_rows_removed": removed})
        return removed

    def budget_for(self, tenant_id: str) -> Budget:
        return self.budgets.get(tenant_id, self.default_budget)

    def record_usage(
        self, tenant_id: str, model: str, prompt_tokens: int, completion_tokens: int
    ) -> float:
        """Price a usage record against the registry. Returns cost in USD."""
        cost = self.registry.get(model).cost_usd(prompt_tokens, completion_tokens)
        now = datetime.fromtimestamp(self.clock.now(), tz=UTC)
        if self.store is not None:
            self.store.append_usage(UsageRecord(
                ts=now.timestamp(), tenant_id=tenant_id, model=model,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                cost_usd=cost,
            ))
        day_key = now.strftime("%Y-%m-%d")
        month_key = now.strftime("%Y-%m")
        day_model = self._daily.setdefault(tenant_id, {}).setdefault(day_key, {})
        day_model[model] = day_model.get(model) or _Totals()
        day_model[model].add(prompt_tokens, completion_tokens, cost)
        month_model = self._monthly.setdefault(tenant_id, {}).setdefault(month_key, {})
        month_model[model] = month_model.get(model) or _Totals()
        month_model[model].add(prompt_tokens, completion_tokens, cost)
        return cost

    def spend(self, tenant_id: str, period: str = "month") -> float:
        """Spend for the current day or month (period in {'day','month'})."""
        now = datetime.fromtimestamp(self.clock.now(), tz=UTC)
        table = self._daily if period == "day" else self._monthly
        key = now.strftime("%Y-%m-%d") if period == "day" else now.strftime("%Y-%m")
        models = table.get(tenant_id, {}).get(key, {})
        return sum(t.cost_usd for t in models.values())

    def status(self, tenant_id: str) -> BudgetStatus:
        budget = self.budget_for(tenant_id)
        now = datetime.fromtimestamp(self.clock.now(), tz=UTC)
        day_of_month = now.day
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        daily = self.spend(tenant_id, "day")
        monthly = self.spend(tenant_id, "month")
        projected = (monthly / day_of_month) * days_in_month if day_of_month > 0 else monthly

        level = BudgetLevel.ok
        reasons: list[str] = []
        if daily >= budget.daily_hard or monthly >= budget.monthly_hard:
            level = BudgetLevel.hard
            reasons.append("hard budget threshold reached")
        elif daily >= budget.daily_soft or monthly >= budget.monthly_soft:
            level = BudgetLevel.soft
            reasons.append("soft budget threshold reached")
        return BudgetStatus(
            tenant_id=tenant_id,
            level=level,
            daily_spend=round(daily, 6),
            monthly_spend=round(monthly, 6),
            projected_month_end=round(projected, 6),
            day_of_month=day_of_month,
            days_in_month=days_in_month,
            reasons=reasons,
        )
