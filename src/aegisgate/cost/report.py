"""Monthly cost report generator: JSON + markdown per tenant and model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aegisgate.cost.meter import CostMeter


class MonthlyReportGenerator:
    def __init__(self, meter: CostMeter) -> None:
        self.meter = meter

    def _collect(self, month_key: str) -> dict[str, Any]:
        tenants: dict[str, Any] = {}
        monthly = self.meter._monthly  # noqa: SLF001 - report owns aggregation
        for tenant_id, months in monthly.items():
            models = months.get(month_key, {})
            if not models:
                continue
            tenant_totals = {
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
                "by_model": {},
            }
            for model, totals in models.items():
                tenant_totals["by_model"][model] = {
                    "requests": totals.requests,
                    "prompt_tokens": totals.prompt_tokens,
                    "completion_tokens": totals.completion_tokens,
                    "cost_usd": round(totals.cost_usd, 6),
                }
                tenant_totals["requests"] += totals.requests
                tenant_totals["prompt_tokens"] += totals.prompt_tokens
                tenant_totals["completion_tokens"] += totals.completion_tokens
                tenant_totals["cost_usd"] = round(
                    tenant_totals["cost_usd"] + totals.cost_usd, 6
                )
            tenants[tenant_id] = tenant_totals
        return tenants

    def generate(self, month: str | None = None) -> tuple[dict[str, Any], str]:
        """Return (report_dict, markdown_report) for a YYYY-MM month."""
        if month is None:
            month = datetime.fromtimestamp(self.meter.clock.now(), tz=UTC).strftime("%Y-%m")
        tenants = self._collect(month)
        total_cost = round(sum(t["cost_usd"] for t in tenants.values()), 6)
        report = {"month": month, "total_cost_usd": total_cost, "tenants": tenants}
        return report, self._markdown(report)

    @staticmethod
    def _markdown(report: dict[str, Any]) -> str:
        lines = [
            f"# AegisGate cost report — {report['month']}",
            "",
            f"Total spend: **${report['total_cost_usd']:.4f}**",
            "",
        ]
        if not report["tenants"]:
            lines.append("_No usage recorded for this period._")
            return "\n".join(lines)
        lines.append("| Tenant | Requests | Prompt tok | Completion tok | Cost (USD) |")
        lines.append("| --- | --- | --- | --- | --- |")
        for tenant, totals in report["tenants"].items():
            lines.append(
                f"| {tenant} | {totals['requests']} | {totals['prompt_tokens']} "
                f"| {totals['completion_tokens']} | {totals['cost_usd']:.4f} |"
            )
        lines.append("")
        for tenant, totals in report["tenants"].items():
            lines.append(f"## {tenant}")
            lines.append("")
            lines.append("| Model | Requests | Cost (USD) |")
            lines.append("| --- | --- | --- |")
            for model, mt in sorted(totals["by_model"].items()):
                lines.append(f"| {model} | {mt['requests']} | {mt['cost_usd']:.4f} |")
            lines.append("")
        return "\n".join(lines)
