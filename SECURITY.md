# Security Policy — AegisGate

A gateway sits on the most sensitive path in an LLM stack: every prompt, every
tenant's tokens, every provider credential. The threat model and the enforced
defenses:

| Threat | Defense |
|---|---|
| Tenant impersonation | Bearer-token → tenant mapping on every request; unknown tokens rejected before routing (tested in the ASGI suite) |
| Cross-tenant data leakage via the semantic cache | Cache keys are namespaced by tenant; cached answers are deep-copied per hit |
| Per-tenant resource exhaustion | Token-bucket rate limits per `(tenant, model)` with `Retry-After`; hard monthly budgets can hard-block a tenant |
| Provider credential exposure | Provider keys live in provider config/env only; the gateway proxies and never echoes them; `/v1/models` exposes capabilities, not credentials |
| Prompt/log exfiltration | Canaries and scrubbing belong to the calling app — the gateway's contribution is span-level observability (ForensiQ-compatible) so exfil patterns are visible; no prompt content is written to metrics |
| Kill-switch abuse | Flag/kill-switch mutations are file-persisted and auditable; rollout percentage is deterministic per subject so a kill switch is instantly global |
| DoS via streaming | SSE pass-through is bounded by per-tenant limits; breaker half-open probes are quota'd so recovery storms cannot amplify |

## Deployment guidance

- Run behind TLS (reverse proxy or service mesh); the gateway itself is transport-agnostic.
- Multi-instance deployments MUST share the `RateLimitStore` and budget stores (Redis implementation provided; in-memory stores are single-process only).
- `docker-compose.yml` ships the production topology (gateway + Redis + Langfuse).
- Report issues marked `security`.

## Identity, durability, and audit (v2 hardening)

| Control | Implementation |
|---|---|
| **Real credentials** | `StaticTokenProvider` stores SHA-256 hashes at rest (raw tokens never persist on the object or in logs), compares constant-time, and supports a rotation window (`AEGISGATE_TENANT_TOKENS_PREVIOUS`). `JwtProvider` validates HS256 signature + `exp` + `aud` + `iss` (opt-in `AEGISGATE_AUTH_MODE=jwt`, fail-closed startup if misconfigured). |
| **Durable metering** | `AEGISGATE_LEDGER_PATH` enables the SQLite/WAL append-only usage ledger. Spend survives restarts — the autopilot hydrates month-to-date totals from the ledger at boot, so budget enforcement cannot be reset by a pod restart. |
| **GDPR erasure** | `DELETE /admin/tenants/{id}` removes a tenant's usage rows and budgets across memory + ledger; the erasure itself is written to the append-only audit log (pseudonymized, tamper-evident). |
| **Audit trail** | Every admin action (budget changes, tenant deletion) is appended to the audit log; `GET /admin/audit` (admin-token guarded, constant-time compare) exposes the tail. |
| **Admin API** | Disabled unless `AEGISGATE_ADMIN_TOKEN` is set; token compared as SHA-256 with `hmac.compare_digest`. |
| **Correlation IDs** | Every request carries `X-Correlation-ID` (inbound honored) and emits one structured-JSON access line — the join key across gateway, app, and ForensiQ spans. |
| **Measured load profile** | `scripts/load_profile.py` publishes p50/p95/p99 + throughput as a CI artifact; SLO targets and alert rules live in `monitoring/alerts.yml` with the on-call `docs/RUNBOOK.md`. |
