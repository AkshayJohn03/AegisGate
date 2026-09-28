"""Durable metering: append-only usage ledger + budgets + audit log (SQLite/WAL).

This is what turns the cost autopilot from a dashboard into a system of
record: spend survives restarts, records are never updated or deleted in
place (corrections are new rows), and GDPR tenant deletion is explicit and
itself audited.

``SQLiteMeterStore`` uses stdlib sqlite3 with WAL — no infra dependency, so
the whole thing stays testable offline. ``InMemoryMeterStore`` remains the
fallback for tests that don't want a tmp file.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class UsageRecord:
    ts: float
    tenant_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    request_id: str = ""


class MeterStore(Protocol):
    def append_usage(self, record: UsageRecord) -> None: ...
    def month_spend(self, tenant_id: str, month_key: str) -> float: ...
    def day_spend(self, tenant_id: str, day_key: str) -> float: ...
    def usage_rows(self, tenant_id: str, month_key: str) -> list[UsageRecord]: ...
    def set_budget(self, tenant_id: str, budget_json: str) -> None: ...
    def get_budget(self, tenant_id: str) -> str | None: ...
    def audit(self, actor: str, action: str, target: str, detail: dict[str, Any]) -> None: ...
    def audit_tail(self, limit: int = 50) -> list[dict[str, Any]]: ...
    def delete_tenant(self, tenant_id: str) -> int: ...
    def list_tenants(self) -> list[str]: ...


class InMemoryMeterStore:
    def __init__(self) -> None:
        self._usage: list[UsageRecord] = []
        self._budgets: dict[str, str] = {}
        self._audit: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def append_usage(self, record: UsageRecord) -> None:
        with self._lock:
            self._usage.append(record)

    def month_spend(self, tenant_id: str, month_key: str) -> float:
        return sum(
            r.cost_usd for r in self._usage
            if r.tenant_id == tenant_id and time.strftime("%Y-%m", time.gmtime(r.ts)) == month_key
        )

    def day_spend(self, tenant_id: str, day_key: str) -> float:
        return sum(
            r.cost_usd for r in self._usage
            if r.tenant_id == tenant_id and time.strftime("%Y-%m-%d", time.gmtime(r.ts)) == day_key
        )

    def usage_rows(self, tenant_id: str, month_key: str) -> list[UsageRecord]:
        return [
            r for r in self._usage
            if r.tenant_id == tenant_id and time.strftime("%Y-%m", time.gmtime(r.ts)) == month_key
        ]

    def set_budget(self, tenant_id: str, budget_json: str) -> None:
        self._budgets[tenant_id] = budget_json

    def get_budget(self, tenant_id: str) -> str | None:
        return self._budgets.get(tenant_id)

    def audit(self, actor: str, action: str, target: str, detail: dict[str, Any]) -> None:
        with self._lock:
            self._audit.append({
                "ts": time.time(), "actor": actor, "action": action,
                "target": target, "detail": detail,
            })

    def audit_tail(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._audit[-limit:])

    def delete_tenant(self, tenant_id: str) -> int:
        with self._lock:
            before = len(self._usage)
            self._usage = [r for r in self._usage if r.tenant_id != tenant_id]
            self._budgets.pop(tenant_id, None)
            return before - len(self._usage)

    def list_tenants(self) -> list[str]:
        return sorted({r.tenant_id for r in self._usage})


class SQLiteMeterStore:
    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS usage_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL, tenant_id TEXT NOT NULL, model TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL,
                    cost_usd REAL NOT NULL, request_id TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS ix_usage_tenant_ts ON usage_ledger(tenant_id, ts);
                CREATE TABLE IF NOT EXISTS budgets (
                    tenant_id TEXT PRIMARY KEY, budget_json TEXT NOT NULL,
                    updated_ts REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
                    target TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            self._conn.commit()

    def append_usage(self, record: UsageRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO usage_ledger (ts, tenant_id, model, prompt_tokens, "
                "completion_tokens, cost_usd, request_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record.ts, record.tenant_id, record.model, record.prompt_tokens,
                 record.completion_tokens, record.cost_usd, record.request_id),
            )
            self._conn.commit()

    def month_spend(self, tenant_id: str, month_key: str) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_ledger "
                "WHERE tenant_id = ? AND strftime('%Y-%m', ts, 'unixepoch') = ?",
                (tenant_id, month_key),
            ).fetchone()
        return float(row[0])

    def day_spend(self, tenant_id: str, day_key: str) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_ledger "
                "WHERE tenant_id = ? AND strftime('%Y-%m-%d', ts, 'unixepoch') = ?",
                (tenant_id, day_key),
            ).fetchone()
        return float(row[0])

    def usage_rows(self, tenant_id: str, month_key: str) -> list[UsageRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, model, prompt_tokens, completion_tokens, cost_usd, request_id "
                "FROM usage_ledger WHERE tenant_id = ? "
                "AND strftime('%Y-%m', ts, 'unixepoch') = ? ORDER BY id",
                (tenant_id, month_key),
            ).fetchall()
        return [
            UsageRecord(ts=r[0], tenant_id=tenant_id, model=r[1], prompt_tokens=r[2],
                        completion_tokens=r[3], cost_usd=r[4], request_id=r[5])
            for r in rows
        ]

    def set_budget(self, tenant_id: str, budget_json: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO budgets (tenant_id, budget_json, updated_ts) VALUES (?, ?, ?) "
                "ON CONFLICT(tenant_id) DO UPDATE SET budget_json=excluded.budget_json, "
                "updated_ts=excluded.updated_ts",
                (tenant_id, budget_json, time.time()),
            )
            self._conn.commit()

    def get_budget(self, tenant_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT budget_json FROM budgets WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
        return row[0] if row else None

    def audit(self, actor: str, action: str, target: str, detail: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log (ts, actor, action, target, detail_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (time.time(), actor, action, target, json.dumps(detail)),
            )
            self._conn.commit()

    def audit_tail(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, actor, action, target, detail_json FROM audit_log "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"ts": r[0], "actor": r[1], "action": r[2], "target": r[3], "detail": json.loads(r[4])}
            for r in rows
        ]

    def delete_tenant(self, tenant_id: str) -> int:
        """GDPR erasure: remove usage rows + budget. Returns rows removed.
        The deletion ITSELF is audit-logged by the caller (audit rows for the
        tenant id are pseudonymized, not deleted — the audit trail must stay
        tamper-evident)."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM usage_ledger WHERE tenant_id = ?", (tenant_id,))
            self._conn.execute("DELETE FROM budgets WHERE tenant_id = ?", (tenant_id,))
            self._conn.commit()
        return cur.rowcount

    def list_tenants(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT DISTINCT tenant_id FROM usage_ledger").fetchall()
        return sorted(r[0] for r in rows)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> SQLiteMeterStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
