# AegisGate Runbook (on-call)

## SLOs
- Availability: 99.5% of /v1/chat/completions return non-5xx.
- Latency: p95 < 800ms end-to-end (measured baseline offline: p50 36ms / p95 60ms / p99 68ms at 50 VUs, see `load_profile.json`).
- Durability: usage ledger is WAL SQLite — 0 accepted-but-unrecorded requests (write-ahead on the metering path).

## Alert → action

### AegisGateHighErrorRate (critical)
1. `GET /health` — is the gateway itself up?
2. Check breaker states on `/metrics` (`aegisgate_breaker_state`). A provider in
   `open` means the provider is failing, not the gateway: confirm auto-quarantine
   fired (audit log: `GET /admin/audit`) and that fallback chains served traffic.
3. If ALL providers are open: upstream outage — page the platform owner; the
   kill switch (`flags`) can pin traffic to the healthiest provider manually.

### AegisGateProviderCircuitOpen (warning)
- Expected behavior under a provider incident. Verify half-open probes are
  recovering (`aegisgate_breaker_state` transitions) — recovery is automatic
  after `recovery_timeout`. No action unless open > 15m.

### AegisGateP95LatencyBreach (warning)
1. Check whether requests are being hedged (`aegisgate` field `hedged` in
   responses) — elevated hedging means the primary is slow, check that provider.
2. Check cache hit rate: a collapse pushes full provider latency onto every
   request (see cache alert below).
3. Scale: the gateway is stateless except the ledger — add replicas; Redis is
   the shared rate-limit/budget store (`docker-compose.yml`).

### AegisGateBudgetHardDenials (info)
- A tenant exceeded its hard budget. Expected behavior. Notify the account
  owner; raise via `meter.set_budget` (audited) — never by editing the ledger.

### AegisGateCacheHitRateCollapse (warning)
- Traffic-mix shift or an over-aggressive invalidation. Inspect
  `aegisgate_cache_hits_total` vs lookups; verify skip-rules (temperature,
  tool-calls) haven't started matching most traffic.

## Common operations
- **Rotate tenant credentials**: add the new pair to `AEGISGATE_TENANT_TOKENS`
  and move the old one to `AEGISGATE_TENANT_TOKENS_PREVIOUS` — both work during
  the window, old tokens are flagged `retired`. Never edit the ledger by hand.
- **Offboard a tenant (GDPR)**: `DELETE /admin/tenants/{id}` with the admin
  token — removes usage rows + budgets, writes an audit entry. The audit trail
  itself is retained (tamper-evidence).
- **Verify ledger health**: `GET /admin/audit` after any admin action must show
  the new entry; zero entries after actions means the ledger path is wrong —
  check `AEGISGATE_LEDGER_PATH`.
