"""Measured load profile for the gateway ASGI app (no external tools).

Runs N concurrent virtual users against the in-process app via httpx ASGI
transport for a fixed duration, then reports p50/p95/p99 latency + throughput.
This is the honest, reproducible answer to "does it hold up under load" at
whatever scale the machine running it can produce.

Usage:  python scripts/load_profile.py --vus 50 --seconds 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import httpx

from aegisgate.config import AegisGateSettings
from aegisgate.gateway.app import create_app


async def _worker(client: httpx.AsyncClient, headers: dict,
                  stop_at: float, latencies: list) -> None:
    i = 0
    while time.perf_counter() < stop_at:
        i += 1
        t0 = time.perf_counter()
        resp = await client.post(
            "/v1/chat/completions", headers=headers,
            json={"model": "gpt-4o-mini",
                  "messages": [{"role": "user", "content": f"load probe {i}"}]},
        )
        latencies.append((time.perf_counter() - t0) * 1000)
        if resp.status_code == 429:
            await asyncio.sleep(0.05)  # back off like a well-behaved client


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))
    return ordered[idx]


async def run(vus: int, seconds: float) -> dict:
    settings = AegisGateSettings(tenant_tokens="load:load-tenant")
    app = create_app(settings=settings)
    headers = {"Authorization": "Bearer load"}
    transport = httpx.ASGITransport(app=app)
    latencies: list[float] = []
    errors = 0
    start = time.perf_counter()
    stop_at = start + seconds
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        results = await asyncio.gather(
            *[_worker(client, headers, stop_at, latencies) for _ in range(vus)],
            return_exceptions=True,
        )
        errors = sum(1 for r in results if isinstance(r, Exception))
    wall = time.perf_counter() - start
    return {
        "vus": vus,
        "duration_s": round(wall, 2),
        "requests": len(latencies),
        "errors": errors,
        "throughput_rps": round(len(latencies) / wall, 1),
        "p50_ms": round(_pct(latencies, 50), 2),
        "p95_ms": round(_pct(latencies, 95), 2),
        "p99_ms": round(_pct(latencies, 99), 2),
        "mean_ms": round(statistics.mean(latencies), 2) if latencies else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vus", type=int, default=50)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    report = asyncio.run(run(args.vus, args.seconds))
    print(json.dumps(report, indent=2))
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
