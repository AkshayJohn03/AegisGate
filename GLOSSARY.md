# AegisGate Glossary — every keyword in this repo, in plain English

This is the study companion to the whiteboard explainer video. Read a term,
understand it once, and you can follow the whole system. Each entry tells you
what the word means in everyday language, then "why it matters here" — what
AegisGate uses it for.

---

## Auth & Identity

**LLM (Large Language Model)**
This is the AI that reads text and writes text back — the "expert" your product
is phoning. Each one is hosted by a provider (OpenAI, a local server, etc.) and
each has different prices, speeds, and skill levels.
Why it matters here: AegisGate sits between your product and a fleet of 12 of
these across 9 providers, and decides which one answers each question.

**API gateway**
This is a single door that all traffic walks through before it reaches the real
services behind it. Instead of every part of your app talking to every AI
provider directly, everything goes through one checkpoint that can count,
limit, protect, and reroute.
Why it matters here: AegisGate *is* the gateway — one FastAPI process exposing
an OpenAI-compatible API (`POST /v1/chat/completions`) that every request passes.

**Tenant**
This is one customer or team using your product, kept separate from the others.
Think of flats in a building: everyone shares the building, but each tenant has
their own meter and their own key.
Why it matters here: every request belongs to a tenant, and rate limits,
budgets, caches, and cost meters are all tracked per tenant.

**Bearer token**
This is a secret string a client sends with every request — like a wristband at
a concert that proves you already paid. "Bearer" just means "whoever carries
this band gets in".
Why it matters here: your request carries `Authorization: Bearer sk-demo`, and
AegisGate resolves that token to a tenant before doing anything else.

**JWT (JSON Web Token)**
This is a wristband that carries its own details inside — who you are, when it
expires — in a signed package the server can verify without a lookup. It's an
ID badge that forges badly.
Why it matters here: set `AEGISGATE_AUTH_MODE=jwt` and AegisGate validates
incoming JWTs and reads the `tenant` claim to identify the caller.

**HS256**
This is the specific signing recipe used for those JWTs: a shared secret and
the SHA-256 hash function produce a signature. If anyone alters the badge, the
signature no longer matches and the badge is rejected.
Why it matters here: AegisGate accepts only HS256-signed JWTs and checks the
signature, expiry, audience, and issuer — any problem and the request is
refused (fail closed).

**SHA-256**
This is a hash function: feed it any text and it produces a fixed 64-character
fingerprint. The same text always gives the same fingerprint, but you cannot
rebuild the text from the fingerprint.
Why it matters here: tokens are stored only as SHA-256 fingerprints (so a leak
of the store reveals nothing usable), and cache keys are SHA-256 fingerprints
of the request.

**Constant-time compare**
This is a way of comparing two secrets that always takes the same amount of
time, whether they match or not. A normal comparison gives attackers a timing
clue ("the first 5 characters were right..."), this one gives none.
Why it matters here: token comparison in the auth provider uses
`hmac.compare_digest`, so attackers can't time-sniff valid tokens.

**Key rotation**
This is changing a lock but letting the old key keep working for a grace
period, so nobody is locked out mid-switchover.
Why it matters here: `AEGISGATE_TENANT_TOKENS_PREVIOUS` holds retired tokens
that still authenticate during the rotation window, flagged as `retired`.

## Routing & Resilience

**Model registry**
This is the roster of every available expert: name, provider, price per word
in and out, quality tier, and capabilities. The router can only pick from who
is on the roster.
Why it matters here: `models.yaml` lists 12 models across 9 providers, and the
registry validates it before anything routes.

**Routing score**
This is how the router compares candidates fairly: each model gets a blended
mark from latency, error rate, price, and quality, with weights tuned to what
this request cares about (cheap? fast? smart?). Lowest blended mark wins.
Why it matters here: `ScoredRouter` min-max normalises each signal across the
currently-healthy candidates, so "best" means "best of who is actually
routable right now".

**EWMA (exponentially weighted moving average)**
This is a running average that trusts recent events more than old ones — like
judging a batsman on their last few innings, not their whole career. One bad
innings barely moves it; a real slump shows up within a few samples.
Why it matters here: latency and error rates per model are EWMA-smoothed
(alpha 0.3/0.2), which feeds the routing score and the health monitor without
storage or jitter.

**Circuit breaker**
This is the "don't redial a switched-off phone" rule. After a provider fails a
few times in a row, the breaker trips *open* and requests stop being wasted on
it; later it *half-opens* to let a couple of test calls through, and *closes*
again once they succeed.
Why it matters here: each provider has its own breaker (5 failures to trip,
30s recovery timeout, 2 half-open probes), so one dead provider can't burn
every request.

**Half-open state**
This is the breaker's probation period: the dead provider is allowed a small
quota of test requests while everyone else stays redirected. If the probes
succeed, full service resumes; if not, back to open.
Why it matters here: AegisGate admits 2 concurrent probes in half-open, so a
flappy provider recovers in one cycle instead of flapping forever.

**Retry with jitter**
This is politely redialling after a short wait — but with a random amount of
jitter mixed into the delay so a thousand clients don't all redial at the same
instant (the "thundering herd"). Exponential backoff means each further retry
waits longer.
Why it matters here: AegisGate retries with full jitter, and only retries
errors where retrying makes sense (429s, 5xx, timeouts) — a malformed request
fails fast because trying again is pure waste.

**Request hedging**
This is calling a second expert in parallel because the first is taking too
long — whoever answers first wins, and the other call is cancelled. It buys
tail-latency insurance at the cost of a doubled bill, so it's used sparingly.
Why it matters here: a hedge fires only when the primary has been silent past
0.25s *and* its observed p95 latency is high; the loser is cancelled, not leaked.

**Fallback chain**
This is the ordered list of "who to try next" when the first choice can't take
the call. It has an attempt budget so one bad request can't cascade through
every provider in existence.
Why it matters here: when the primary model fails, the router walks its ordered
fallback candidates, and the response records which model actually served you.

**Auto-quarantine**
This is the health monitor's reflex: when a provider's error rate drifts
abnormally high, the monitor trips its breaker and flips its kill switches by
itself, and emits an alert payload naming the models — no human needed at 2am.
Why it matters here: the EWMA-based health monitor watches every provider and
quarantines failing ones automatically; the runbook tells operators how to
confirm it fired.

## Rate Limiting

**Rate limit**
This is a speed cap on how many requests a client may send in a period — the
bouncer at the door of the API. It protects the service from being hammered
into the ground by one over-eager caller.
Why it matters here: AegisGate rate limits per (tenant, model) pair, so one
tenant's flood can't starve everyone else's budget of capacity.

**Token bucket**
This is the classic trick for fair speed caps: imagine a bucket that holds
tokens and drips them back in at a fixed rate. Each request spends one token;
empty bucket means wait. Bursts are allowed up to the bucket size, but the
long-run average can never exceed the drip rate.
Why it matters here: AegisGate chose token buckets over fixed windows because
windows let clients spend double at the boundary and punish legitimate bursts
with a cliff.

**429 (Too Many Requests)**
This is the HTTP status code meaning "you're going too fast, slow down". It's
the rate limit speaking, not a crash.
Why it matters here: when a tenant's bucket is empty (or their hard budget is
spent), AegisGate returns 429 — the tests cover both paths.

**Retry-After**
This is the polite note attached to a 429: "try again in N seconds". Well-behaved
clients read it instead of hammering blindly.
Why it matters here: AegisGate's 429 responses carry `Retry-After`, and the
load-test client itself backs off like a well-behaved caller.

**Redis**
This is a tiny in-memory database that many servers share — the communal
whiteboard that all replicas of a service can read and write at high speed.
Why it matters here: in-memory rate-limit buckets only work for one process;
the `RateLimitStore` Protocol has a Redis implementation (env-guarded, with
multi-instance refill math) so many gateway replicas can share one set of
buckets.

## Caching

**Semantic cache**
This is a memory that answers "have I essentially answered this before?" — not
by exact text match, but by meaning. If a new question means the same as an
answered one, the stored answer comes back instantly, free.
Why it matters here: AegisGate's cache is two-tier: an exact fingerprint match
first, then a meaning match via embeddings.

**Embedding**
This is a way of turning text into a list of numbers (a vector) where similar
meanings land near each other. It's how a computer can measure "same idea"
without understanding words.
Why it matters here: the cache embeds each request so paraphrases can be
recognised; the bundled embedder is a deterministic hashing one so tests stay
offline, and a real embedding model slots in behind the same Protocol.

**Cosine similarity**
This is the maths for "how close do two vectors point": 1.0 means identical
direction, 0 means unrelated. It compares direction, not length.
Why it matters here: tier 2 of the cache returns a stored answer only when
cosine similarity is ≥ 0.92 — deliberately conservative, because a wrong cache
hit silently corrupts answers while a miss just costs money.

**LRU (Least Recently Used)**
This is the tidy-up rule for a full shelf: evict whatever hasn't been touched
in the longest. Recently used things stay; forgotten things make room.
Why it matters here: the cache is capped at 256 entries; LRU eviction keeps
memory bounded without a configuration headache.

**TTL (Time To Live)**
This is an expiry stamp on each cached answer. After the TTL, the entry is
treated as stale no matter how popular it was.
Why it matters here: cached answers expire after 3600s (an hour), so stale
answers can't linger forever.

**Cache invalidation & skip rules**
This is the discipline of *not* answering from memory when it would be wrong.
Skip rules are the "never cache this" list.
Why it matters here: requests with temperature > 0.7 or tool-calling bypass the
cache entirely — replaying those would be semantically wrong, not merely stale.

## Cost

**Cost autopilot**
This is the smart electricity meter that also turns things off: it meters every
tenant's spend, watches the budget, and when burn gets dangerous it
automatically reroutes easy tasks to cheaper models. No human in the loop.
Why it matters here: `CostAutopilot` classifies each request easy/hard and,
under budget pressure, lowers the allowed quality tier so easy work lands on
cheap models.

**Budget (soft/hard)**
The soft budget is the warning line — "you're on track to overspend"; the hard
budget is the wall — "requests are now refused". Per tenant, per day and month.
Why it matters here: AegisGate's defaults are $5/$10 daily and $50/$100 monthly;
crossing soft triggers downgrades, crossing hard returns 429 with a trace id.

**Burn projection**
This is extrapolation from the meter: if you've spent this much by this day of
the month, at this rate you'll spend roughly that much by month-end. It's the
autopilot's crystal ball.
Why it matters here: the meter projects month-end burn, and the downgrade
policy kicks in when projected burn reaches 80% of the hard budget.

**Model downgrade / quality tier**
Models are ranked 1–5 by capability (5 = frontier). Downgrading means allowing
only lower tiers for a while — you lose some peak quality on easy tasks, you
stop bleeding money.
Why it matters here: the autopilot drops the max-tier *ceiling* by one (and
caps output price on easy tasks) — deliberately not "raise the minimum tier",
which would route to *better* models and accelerate burn.

**Complexity classifier**
This is the judge that decides whether a request is easy or hard — by
inspectable signals like length, code fences, and structured-output cues. A
deterministic heuristic you can read, not a black box.
Why it matters here: easy requests are the ones the autopilot downgrades; a
trained classifier can later replace the heuristic behind the same method
signature.

**Append-only ledger**
This is a receipt book you only ever write in — never erase or scribble over a
line. To "correct" an entry you add a new line. This makes the history
trustworthy and auditable.
Why it matters here: every request's token count and cost is appended to the
usage ledger; records are never updated or deleted in place.

**WAL (write-ahead logging)**
This is the flight-recorder discipline for databases: write the note to the log
*first*, then apply it. If the system crashes mid-operation, the log shows
exactly what was in flight and nothing accepted is ever lost.
Why it matters here: the SQLite usage ledger runs in WAL mode, giving the
guarantee of zero accepted-but-unrecorded requests across restarts.

**GDPR erasure**
This is the European privacy law's right-to-be-forgotten made real: when a
tenant asks to be deleted, all their personal data goes — provably.
Why it matters here: `DELETE /admin/tenants/{id}` removes the tenant's usage
rows and budgets in one operation, and the deletion itself is written to the
audit trail (which is retained for tamper-evidence).

**Audit log**
This is the security camera for administrative actions: who did what to what,
and when. Separate from the data it watches.
Why it matters here: every admin action (budget changes, deletions) is recorded
in the audit log; `GET /admin/audit` shows the trail, and the runbook says to
check it after every admin action.

## Flags

**Feature flag**
This is a switch in code you can flip without redeploying: turn a behaviour on
or off for chosen users while the software runs.
Why it matters here: AegisGate's flag engine controls rollouts, A/B tests,
kill switches, and shadow mode — with state persisted to JSON atomically.

**Percentage rollout**
This is letting 5% of users try the new thing first. If it misbehaves, only 5%
notice instead of everyone.
Why it matters here: new models roll out by percentage through the flag engine
before becoming default for anyone.

**Hash bucketing**
This is how you choose the 5% fairly and repeatably: hash the user's id, and
the hash decides their bucket (say, buckets 0–4 out of 100). Same user, same
bucket, every time — no randomness to store.
Why it matters here: AegisGate's rollouts are deterministic hash bucketing, so
the same request always gets the same decision — which is why the tests can
assert exact percentages.

**Sticky session / sticky assignment**
This is making sure the same user keeps getting the same variant across
requests. Nothing is weirder for a user than the app changing personality
mid-conversation.
Why it matters here: A/B assignment is sticky per user — once bucketed into the
experiment, you stay in it.

**Kill switch**
This is the big red button: one flag that instantly removes a model from
service everywhere, no deploy needed.
Why it matters here: every model can have a kill switch; the pipeline drops
killed models from routing immediately, and the health monitor flips them
automatically when quarantining.

**Shadow mode**
This is the dress rehearsal: the new model receives a mirror copy of real
traffic, its answers are recorded and diffed against the real one — and its
output is *never* shown to a user.
Why it matters here: shadow mode lets AegisGate evaluate a candidate model
against live traffic with zero user risk; the diffs are stored for inspection.

## Observability

**Prometheus**
This is the standard format and system for a server publishing live numbers —
requests served, errors, latencies — that a monitoring system scrapes and
graphs.
Why it matters here: `/metrics` exposes Prometheus text-format counters,
histograms, and gauges (requests by tenant/model/status, cache hits, breaker
states, costs), with alert rules in `monitoring/alerts.yml`.

**p50 / p95 / p99 (percentiles)**
These describe the *typical* and the *unlucky* experience. p50 is the median —
half of requests finished faster than this. p95 means 95 out of 100 requests
finished faster; only the unluckiest 5% were slower. p99 is the "worst regular
experience" line — think of a queue at a coffee shop: p50 is the normal queue,
p99 is the poor soul in the rare bad queue.
Why it matters here: the measured load profile is p50 ~36ms, p95 ~60ms, p99
~68ms — the tail barely rises above the median, which is the whole point of
hedging and breakers.

**Throughput (rps)**
This is how many requests per second the system processed. The pulse rate of
the service.
Why it matters here: the measured load run sustained ~484 requests/second at 50
concurrent virtual users with zero errors.

**SLO (Service Level Objective)**
This is the promise you make in writing about reliability, and the number the
alarms are tuned to protect — e.g. "p95 latency under 800ms".
Why it matters here: the runbook states the SLOs (99.5% availability, p95 <
800ms) and pairs each alert with the exact action an on-call engineer takes.

**Structured logging**
This is writing logs as fields (key: value) rather than prose, so machines can
filter and count them. "tenant=acme model=gpt-4o status=200" beats a sentence.
Why it matters here: every pipeline stage logs structured fields — ids, counts,
model names — and message bodies are never logged by default.

**Correlation ID**
This is a tracking number stamped on one request that appears in every log line
and span it generates, so all the evidence of one journey sticks together.
Why it matters here: every request carries a correlation id through the whole
pipeline, so a 2am investigation starts with one id, not a haystack.

**Span**
This is one segment of a request's timeline — "rate limiting took 0.2ms",
"routing picked model X", "provider call took 40ms" — emitted as a structured
record.
Why it matters here: every stage emits a ForensiQ-compatible span to a pluggable
`sink`, so request-scoped forensic timelines come free.

## Testing

**Unit test**
This is a tiny automated exam for one piece of code: given this input, expect
that output. Hundreds of them run in seconds and catch regressions before
humans do.
Why it matters here: AegisGate has 106 of them, and they run the *real*
pipeline and the *real* FastAPI app — not toys.

**ASGI**
This is the standard interface that lets Python web apps (like FastAPI) talk to
servers. Its magic for testing: you can drive the whole app in-process, with no
network socket at all.
Why it matters here: the gateway tests run over ASGI, so the full request
lifecycle — auth, streaming, 401s, 429s, metrics — is exercised with zero
network.

**Mock / offline test**
This is a test where the unreliable outside world (real AI providers, real
clocks, real networks) is replaced by a deterministic stand-in, so results are
identical every run.
Why it matters here: everything runs offline by construction — the
`EchoMockClient` stands in for providers, a `ManualClock` controls time, and
the whole suite passes in about 1.5 seconds with no keys and no network.

---

## The measured numbers, in one place

| Number | What it says in plain words |
|---|---|
| 106 tests | every mechanism above has an automated exam, all passing |
| 2,926 requests | how many calls the load test pushed through the real gateway |
| 0 errors | not one of them failed |
| 484 rps | requests handled per second at 50 concurrent users |
| p50 ~36ms | the typical request took about a third of a tenth of a second |
| p95 ~60ms | 95% of requests finished within 60ms — the tail is barely above the median |
| p99 ~68ms | even the unlucky 1% waited only 68ms |
