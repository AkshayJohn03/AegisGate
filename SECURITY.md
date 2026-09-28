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
