"""Minimal Prometheus text-format metrics registry.

Counters, gauges and a fixed-bucket histogram, rendered in the Prometheus
text exposition format. Deliberately dependency-free: pulling in
prometheus_client for this much surface isn't worth the weight.
"""

from __future__ import annotations

from typing import Any

LabelKey = tuple[tuple[str, str], ...]


def _key(name: str, labels: dict[str, str] | None) -> LabelKey:
    return tuple(sorted((labels or {}).items()))


def _render_labels(labels: LabelKey, extra: dict[str, str] | None = None) -> str:
    merged = dict(labels)
    if extra:
        merged.update(extra)
    if not merged:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in merged.items())
    return "{" + inner + "}"


class Metrics:
    HIST_BUCKETS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)

    def __init__(self) -> None:
        self._counters: dict[tuple[str, LabelKey], float] = {}
        self._gauges: dict[tuple[str, LabelKey], float] = {}
        self._histograms: dict[tuple[str, LabelKey], list[float]] = {}

    def inc(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        key = (name, _key(name, labels))
        self._counters[key] = self._counters.get(key, 0.0) + value

    def gauge(self, name: str, labels: dict[str, str] | None = None, value: float = 0.0) -> None:
        self._gauges[(name, _key(name, labels))] = value

    def observe(self, name: str, labels: dict[str, str] | None = None, value: float = 0.0) -> None:
        self._histograms.setdefault((name, _key(name, labels)), []).append(float(value))

    def render(self) -> str:
        lines: list[str] = []
        for (name, labels), value in sorted(self._counters.items()):
            lines.append(f"{name}{_render_labels(labels)} {value}")
        for (name, labels), value in sorted(self._gauges.items()):
            lines.append(f"{name}{_render_labels(labels)} {value}")
        for (name, labels), samples in sorted(self._histograms.items()):
            count = len(samples)
            total = sum(samples)
            for bucket in self.HIST_BUCKETS:
                le = sum(1 for s in samples if s <= bucket)
                lines.append(
                    f'{name}_bucket{_render_labels(labels, {"le": str(bucket)})} {le}'
                )
            lines.append(f'{name}_bucket{_render_labels(labels, {"le": "+Inf"})} {count}')
            lines.append(f"{name}_sum{_render_labels(labels)} {total}")
            lines.append(f"{name}_count{_render_labels(labels)} {count}")
        return "\n".join(lines) + ("\n" if lines else "")

    def snapshot(self) -> dict[str, Any]:
        def keyed(items: dict[tuple[str, LabelKey], float]) -> dict[str, float]:
            return {f"{name}|{labels}": value for (name, labels), value in items.items()}

        return {
            "counters": keyed(self._counters),
            "gauges": keyed(self._gauges),
            "histogram_series": len(self._histograms),
        }
