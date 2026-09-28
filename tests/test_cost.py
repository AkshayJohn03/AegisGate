"""Cost metering, budgets, autopilot policy, complexity classifier, reports."""

from __future__ import annotations

import pytest

from aegisgate.cost.autopilot import (
    AutopilotConfig,
    Complexity,
    ComplexityClassifier,
    CostAutopilot,
)
from aegisgate.cost.meter import Budget, BudgetLevel, CostMeter
from aegisgate.cost.report import MonthlyReportGenerator
from aegisgate.llm.base import Message

# -- meter -------------------------------------------------------------------


def test_meter_prices_from_registry(registry, clock):
    meter = CostMeter(registry, clock)
    cost = meter.record_usage("acme", "gpt-4o-mini", 1_000_000, 1_000_000)
    assert cost == pytest.approx(0.15 + 0.60)
    assert meter.spend("acme", "month") == pytest.approx(0.75)


def test_budget_levels_soft_then_hard(registry, clock):
    meter = CostMeter(
        registry,
        clock,
        default_budget=Budget(daily_soft=0.1, daily_hard=0.2, monthly_soft=0.5, monthly_hard=1.0),
    )
    meter.record_usage("acme", "gpt-4o", 40_000, 0)  # $0.10 -> soft
    assert meter.status("acme").level is BudgetLevel.soft
    meter.record_usage("acme", "gpt-4o", 40_000, 0)  # $0.20 -> hard (monthly also >= 0.5)
    assert meter.status("acme").level is BudgetLevel.hard


def test_month_end_burn_projection(registry, clock):
    # ManualClock default lands on day 14 of a 30-day month.
    meter = CostMeter(registry, clock)
    meter.record_usage("acme", "claude-sonnet-4", 0, 250_000)  # $3.75
    status = meter.status("acme")
    assert status.day_of_month == 14
    assert status.days_in_month == 30
    assert status.projected_month_end == pytest.approx(3.75 / 14 * 30)


# -- complexity classifier -----------------------------------------------------


def test_classifier_easy_medium_hard():
    classifier = ComplexityClassifier()
    assert classifier.classify([Message(content="hi")]) is Complexity.easy
    long_plain = Message(content="x" * 700)
    assert classifier.classify([long_plain]) is Complexity.medium
    code = Message(content="```python\n" + "x" * 1500 + "\n```")
    assert classifier.classify([code]) is Complexity.hard


def test_classifier_structured_demand_is_hard():
    classifier = ComplexityClassifier()
    long_json = Message(content="return " + "a" * 500 + " as json")
    assert classifier.classify([long_json]) is Complexity.hard
    # a short tool-calling prompt is routine; a long one is hard
    assert classifier.classify([Message(content="list files")], has_tools=True) is Complexity.medium
    assert classifier.classify([Message(content="b" * 500)], has_tools=True) is Complexity.hard


# -- autopilot -------------------------------------------------------------------


def test_autopilot_downgrades_tier_under_budget_pressure(registry, clock):
    """Projected month-end burn > 80% of hard budget -> max tier dropped by 1
    (premium traffic exempt)."""
    meter = CostMeter(registry, clock, default_budget=Budget(monthly_hard=10.0))
    meter.record_usage("acme", "claude-sonnet-4", 0, 250_000)  # $3.75 -> projected ~$8.04
    autopilot = CostAutopilot(meter)
    messages = [Message(content="x" * 700)]  # medium complexity
    adjustment = autopilot.adjust("acme", messages)
    assert adjustment.max_quality_tier == 4
    assert not adjustment.admits(registry.get("gpt-4o"))  # tier 5 excluded
    assert adjustment.admits(registry.get("gemini-2.0-flash"))  # tier 4 still allowed
    assert "downgrad" in adjustment.reason

    premium = autopilot.adjust("acme", messages, premium=True)
    assert premium.max_quality_tier == 5


def test_autopilot_hard_budget_blocks(registry, clock):
    meter = CostMeter(registry, clock, default_budget=Budget(daily_hard=0.01, monthly_hard=1000))
    meter.record_usage("acme", "gpt-4o", 10_000, 0)  # $0.025 > daily hard
    autopilot = CostAutopilot(meter)
    adjustment = autopilot.adjust("acme", [Message(content="hi")])
    assert adjustment.allow is False


def test_autopilot_easy_tasks_get_price_cap(registry, clock):
    meter = CostMeter(registry, clock)
    autopilot = CostAutopilot(meter, config=AutopilotConfig(easy_output_price_cap=2.0))
    adjustment = autopilot.adjust("acme", [Message(content="hi")])
    assert adjustment.complexity is Complexity.easy
    assert adjustment.max_output_price == 2.0
    assert not adjustment.admits(registry.get("gpt-4o"))  # $10 output price
    assert adjustment.admits(registry.get("gpt-4o-mini"))


def test_autopilot_no_adjustment_when_healthy(registry, clock):
    meter = CostMeter(registry, clock)
    autopilot = CostAutopilot(meter)
    adjustment = autopilot.adjust("acme", [Message(content="x" * 700)])
    assert adjustment.allow
    assert adjustment.max_quality_tier == 5
    assert adjustment.max_output_price is None


# -- reports ---------------------------------------------------------------------


def test_monthly_report_json_and_markdown(registry, clock):
    meter = CostMeter(registry, clock)
    meter.record_usage("acme", "gpt-4o-mini", 100_000, 50_000)
    meter.record_usage("acme", "gpt-4o", 10_000, 5_000)
    meter.record_usage("globex", "gemini-2.0-flash", 200_000, 10_000)
    report, markdown = MonthlyReportGenerator(meter).generate()
    assert report["tenants"]["acme"]["requests"] == 2
    assert report["tenants"]["acme"]["cost_usd"] > 0
    assert "gpt-4o-mini" in report["tenants"]["acme"]["by_model"]
    assert "| acme |" in markdown
    assert "## globex" in markdown
