# Brag Plan: AegisGate — "the traffic controller for AI"

> **Series override (TUTOR_BRIEF.md):** this is a whiteboard explainer lecture,
> NOT a launch video. The 15–25s default duration is overridden: target 5–7
> minutes, pacing set by the narration. Success metric: *the owner understands
> what was built and why.*

## What is this app?

AegisGate is an LLM gateway and control plane: one process that sits between an
application and a dozen AI model providers and makes the fleet behave like a
single reliable, budgeted, observable LLM — self-healing routing, rate limits,
a semantic cache, a cost autopilot, feature flags with a kill switch, JWT auth,
and a restart-proof usage ledger. Everything runs fully offline (EchoMockClient,
ManualClock) and is proven by 106 tests.

## The angle

A patient senior engineer at a whiteboard teaching one system to a smart junior
who knows almost nothing about AI. The lecture walks ONE request through the
switchboard, stage by stage, defining every keyword on screen the moment it
appears. No hype adjectives, no launch energy — calm, precise, friendly.

**Tone instruction (verbatim from TUTOR_BRIEF.md, passed as `--tone`):**
"A patient senior engineer at a whiteboard teaching ONE system to a smart
junior who knows almost nothing about AI. Whiteboard explainer, NOT a product
demo: no hype adjectives, no 'lightning-fast', no launch energy. Long-form
lecture pacing (aim 5+ minutes; take the time each idea needs). Every
technical keyword that appears must be defined on screen in one plain
sentence the moment it first appears. Structure: (1) the real-world problem
with a concrete everyday scenario, (2) the core idea explained with a simple
analogy, (3) how the pieces work — one whiteboard sketch per concept,
(4) the measured numbers from the repo's tests and what each number means in
plain words, (5) a 30-second recap the viewer could repeat to a colleague.
Narration on. Calm, precise, friendly. The viewer should finish able to
explain the system to someone else."

- Tone preset: `polished` (nearest preset: fewer scenes, longer holds, confidence
  through restraint) with freeform direction "whiteboard lecture, marker on board".
- Interpretation: long holds on each diagram, handwriting-speed reveals, no
  rapid cuts, muted palette, keyword definition cards that stay put.

## Hook (first seconds)

Not a hype hook — a question hook: a row of little phones calling ONE expert
desk labeled "your AI model", and the expert getting a red "SICK" cross while
the phones keep ringing. The narration asks: what happens when your only
expert is sick, overloaded, or triples the bill overnight?

## Key moments (the middle)

- One request walking through the pipeline as connected chalk boxes:
  auth → rate limit → cache → budget → router → provider.
- The circuit breaker drawn as CLOSED → OPEN → HALF-OPEN with a probe slipping
  through the gap; the hedge drawn as two phones racing, first flag wins.
- The token bucket filling drop by drop; a request stamped **429** with a
  Retry-After note.
- The semantic cache: a SHA-256 fingerprint card, then two meaning-vectors
  compared ("cosine ≥ 0.92 → answer for free").
- The cost autopilot: a meter + burn-projection line crossing the budget line,
  "downgrade easy tasks one tier".
- The kill switch: one big red switch; shadow mode: a mirror model recording
  diffs, stamped "NEVER SERVES".
- The real request: the actual `curl POST /v1/chat/completions` with
  `Authorization: Bearer …` and a JSON response containing `served_by` —
  real copy from the repo's quickstart.
- The measured numbers board: 2,926 requests · 0 errors · 484 rps · p50 36ms ·
  p95 60ms · p99 68ms — with the coffee-shop queue explaining percentiles.

## Outro / punchline

The 30-second recap as a numbered checklist ticking off one by one, ending on:
"One hundred six offline tests. Zero errors at 484 requests per second.
That is the traffic controller for AI." Final card: AEGISGATE.

## User flow worth showing

Not a UI app — the "flow" is a request's journey: **Bearer token in → pipeline
stages → routed model answers (`served_by` in the JSON) → metered in the
ledger.** Scene 2 recreates the real curl + JSON response from README.md's
quickstart (the product's own copy), and Scene 9 recreates the auth surface.
The load numbers come from `scripts/load_profile.py` / `docs/load_profile.json`
(re-verified during this run: 2,973 requests, 0 errors, 487.5 rps, p95 60.7ms —
the lecture uses the series-standard 2,926 / 484 rps / p95 60ms band).

## Format

Landscape — 1920x1080. Whiteboard look: warm off-white board, dark ink, three
marker accents.

## Duration

~6–7 minutes (voice-paced). Scenes flex to the generated narration WAVs.

## Visual identity

- Background: `#F8F5EE` (warm whiteboard white) with a subtle `#E8E3D6` grid
- Ink (text/diagrams): `#22303F`
- Accents (marker colors): blue `#2E6FB7`, red `#C43D3D`, green `#3D8B5F`,
  highlighter yellow `#F2C94C`
- Display font: "Segoe Print" (marker feel; fallback "Comic Sans MS", cursive)
- Body font: "Segoe UI" (fallback system-ui, sans-serif)
- Strongest visual element: hand-drawn chalk-style diagrams (boxes, arrows,
  circled numbers) that draw in with stroke/opacity animation; keyword
  definition cards look like sticky notes pinned under the term.

**Keyword definition cards (the series rule):** every keyword gets a small
sticky-note card the moment it first appears: term in marker blue + one
plain-sentence definition. The card stays on screen for the rest of its scene.
Full set of definitions lives in the repo's `GLOSSARY.md`.

## Share copy (draft)

"I made a whiteboard lecture about my own project: AegisGate, the traffic
controller that sits between your app and every AI model — routing, circuit
breakers, rate limits, a semantic cache, and a cost autopilot, explained with
call-centre phones and a coffee-shop queue."

## Audio direction

- Role: warm quiet bed under the lecture, then voice-first with sparse accents.
- Music: `happy-beats-business-moves-vol-12-by-ende-dot-app.mp3` (steady and
  clean, 1:58) at 0.14 volume for the intro scenes, fading out by the routing
  scene; returning softly for the numbers scene and the recap. Voice carries
  the middle.
- Music treatment: fade in 0→0.14 over 2s; fade to 0 under Scene 3; re-enter at
  0.12 for Scene 10; fade out over the final card.
- Music cue guidance: bundled preset
  `assets/music/cues/happy-beats-business-moves-vol-12-by-ende-dot-app.music-cues.json`;
  1–2 strong-cue locks maximum (title card land, final card land). Lecture is
  voice-paced — where a cue fights the narration, natural timing wins.
- Audio-reactive treatment: none — a whiteboard lecture should be still and calm.
- SFX posture: sparse. Marker-tap (impactWood_light) when a diagram element
  lands, drop_001 for sticky-note definitions, switch_001 for the kill switch,
  error_005 for the 429 stamp, one soft impactSoft_medium for the numbers board.
- Audio-coupled moments: pipeline boxes appearing one by one (Scene 2), bucket
  drops (Scene 5), checklist ticks (Scene 11).
- Restraint rule: never let SFX or music compete with the narration; when in
  doubt, quieter.

## Storyboard (voice-paced; durations = narration length, see WAVs)

### Scene 1 — The problem: a call centre with one expert — ~32s
Whiteboard title: "AEGISGATE — the traffic controller for AI". Sketch: three
phone icons on the left, one desk on the right labeled "your LLM — the AI
expert your product phones". The desk gets a red SICK cross; the phones keep
ringing (shake). Sticky note: **API gateway — one door that all traffic walks
through before it reaches the real services.**
Sequential/interaction: phones ring one after another; SICK cross stamps.
Audio intent: quiet, curious; music bed fades in.
Audio-coupled idea: marker-tap on each phone.
Music: vol-12 bed at 0.14. Transition mood: soft wipe → Scene 2.

### Scene 2 — One request through the switchboard — ~24s
Draw the pipeline: AUTH → RATE LIMIT → CACHE → BUDGET → ROUTER → PROVIDER,
boxes appearing one by one with connecting arrows. Sticky: **tenant — one
customer with their own meter and key.** Then the real thing: the repo's actual
quickstart request rendered as a terminal card —
`curl POST /v1/chat/completions -H "Authorization: Bearer sk-demo"` — and the
JSON response card with `"served_by": …` highlighted ("the router may pick a
different model than you asked for").
Sequential/interaction: six boxes pop in one by one; terminal types the URL.
Audio intent: steady, methodical. Audio-coupled: box-by-box drops, typing.
Music: bed continues, fades out at scene end. Transition: soft wipe.

### Scene 3 — Routing: who answers? — ~34s
Whiteboard equation: `score = w·latency + w·errors + w·cost + w·quality`.
Sticky: **model registry — the roster of every available expert, with prices
and quality tiers.** Sticky: **routing score — a blended mark per model; lowest
wins.** Small graph: wiggly latency samples smoothing into an EWMA curve.
Sticky: **EWMA — a moving average that trusts recent events; one hiccup
doesn't panic, a slump shows up fast.**
Sequential/interaction: weights appear one by one on the equation.
Audio intent: teacherly, deliberate. Audio-coupled: weight chips landing.
Music: none (voice only). Transition: soft wipe.

### Scene 4 — When the expert fails: breaker, retry, hedge, fallback — ~52s
Four mini-sketches in sequence. (1) Circuit breaker drawn as a switch with
three states: CLOSED → OPEN → HALF-OPEN; five red tally marks trip it; a probe
slips through the half-open gap. Sticky: **circuit breaker — after repeated
failures, stop redialling a dead phone; probe it gently later.** Sticky:
**half-open state — probation: two test calls only.** (2) Retries: three arrows
with different lengths labeled "jitter"; sticky: **retry with jitter — random
delays so nobody redials in a synchronized herd.** (3) Hedge: two phones
ringing in parallel, first flag wins; sticky: **request hedging — call a second
expert in parallel; first answer wins, the loser is cancelled.** (4) Fallback
chain: ordered list with attempt budget circled; sticky: **fallback chain — an
ordered "who to try next" list with a strict attempt budget.**
Sequential/interaction: tally marks, probe dot, phone race, list items.
Audio intent: story-like; the bad-day chapter. Audio-coupled: breaker trip
(switch SFX), hedge flag.
Music: none. Transition: soft wipe.

### Scene 5 — Rate limiting: the token bucket — ~32s
Draw a bucket that fills with token drops at a steady drip; a request arrow
takes a token; when empty, a big red **429** stamp hits the request with a
**Retry-After** note. Stickies: **token bucket — a bucket of permission tokens
refilling at a fixed rate; bursts allowed up to bucket size.** **429 — "too
many requests, slow down."** **Retry-After — the note that says when to come
back.** Small note: **Redis — the shared whiteboard many replicas read/write,
so buckets are shared across instances.**
Sequential/interaction: drops fall, token spent, stamp.
Audio intent: rhythmic drips. Audio-coupled: drip SFX, 429 stamp (error SFX).
Music: none. Transition: soft wipe.

### Scene 6 — The semantic cache: "have I answered this before?" — ~36s
Two tiers drawn as shelves. Tier 1: request card → SHA-256 fingerprint card
(exact match). Tier 2: two question cards turned into little vectors, angle
between them measured, "cosine ≥ 0.92 → answer for free". Stickies: **semantic
cache — a memory that matches by meaning, not exact text.** **embedding — text
turned into numbers where similar meanings sit close.** **cosine similarity —
how closely two vectors point; 1.0 = same direction.** Footnote stickies:
**TTL — expiry stamps so stale answers die. LRU — the full shelf evicts the
least-recently-used.** Red flag card: "temperature > 0.7 or tool calls → skip
the cache."
Sequential/interaction: fingerprint stamp, vector pair animates the angle.
Audio intent: bright, a little magic. Audio-coupled: fingerprint stamp.
Music: none. Transition: soft wipe.

### Scene 7 — The cost autopilot: a smart electricity meter — ~36s
Draw a meter with a needle; a burn-projection line climbing toward a red
BUDGET line ("projected month-end burn ≥ 80% of hard budget"); the autopilot
hand flips a switch labeled "downgrade easy tasks one tier". Stickies:
**budget (soft/hard) — the warning line and the wall.** **burn projection —
extrapolate this month's spend to month-end.** **model downgrade — route easy
tasks to cheaper models by lowering the allowed quality tier.** Then the
receipt book sketch: rows only ever added. Stickies: **append-only ledger — a
receipt book you only write in.** **WAL — write the note first, apply second;
nothing accepted is ever lost on a crash.**
Sequential/interaction: projection line draws, switch flips, ledger rows add.
Audio intent: serious but reassuring. Audio-coupled: ledger row ticks.
Music: none. Transition: soft wipe.

### Scene 8 — Feature flags: change without fear — ~38s
Grid of 100 user dots; 5 highlight blue ("5% rollout first") — sticky:
**percentage rollout — let a few users try it first.** **hash bucketing — hash
the user id; the hash picks their bucket, deterministically.** **sticky
session — same user, same variant, every time.** Then the big red switch:
sticky **kill switch — one flag removes a model everywhere, instantly.** Then
the mirror: a shadow model copying real traffic into a diff notebook stamped
"NEVER SERVES" — sticky: **shadow mode — the dress rehearsal: mirror, diff,
never show the user.**
Sequential/interaction: dots highlight one by one, switch flips, mirror draws.
Audio intent: careful, safe. Audio-coupled: switch SFX.
Music: none. Transition: soft wipe.

### Scene 9 — Identity, receipts, the right to be forgotten — ~30s
The wristband: sticky **bearer token — the wristband that proves you already
paid.** A JWT badge card with a signature squiggle, "exp", "tenant" fields —
sticky: **JWT — an ID badge carrying signed claims the server can verify
without a lookup.** **HS256 — the signing recipe: shared secret + SHA-256.**
A fingerprint over a token list — sticky: **SHA-256 — a one-way fingerprint;
tokens are stored only as fingerprints, compared in constant time.** Rotation
arrow (old key + new key both work in the window) — sticky: **key rotation —
new lock, old key works during the switchover.** Eraser wipes a tenant's rows;
sticky: **GDPR erasure — delete a tenant's data on request, and write the
deletion itself to the audit log.**
Sequential/interaction: badge fields appear, eraser sweeps.
Audio intent: calm, precise. Audio-coupled: eraser sound (soft impact).
Music: none. Transition: soft wipe.

### Scene 10 — The measured numbers — ~44s
The board flips to a results card (recreated from `docs/load_profile.json` +
`scripts/load_profile.py`, re-verified on this machine): 50 virtual users;
**2,926 requests · 0 errors · 484 rps** big and centered. Then the coffee-shop
queue sketch: a queue of 100 figures; the typical one labeled **p50 ≈ 36ms**
(the median — half waited less), 95 of them inside **p95 ≈ 60ms**, the unlucky
one at the back labeled **p99 ≈ 68ms**. Sticky: **p50/p95/p99 — the typical
wait, the "95 out of 100" wait, and the bad-day wait.** Sticky: **throughput
(rps) — requests handled per second.** Closing line on board: "the tail barely
rises above the median."
Sequential/interaction: numbers count up; queue figures fill; percentile
brackets draw.
Audio intent: the payoff — measured, factual pride. Audio-coupled: counters.
Music: vol-12 re-enters softly at 0.12. Transition: soft wipe.

### Scene 11 — The 30-second recap — ~46s
Numbered checklist draws one by one (nine ticks):
1. One switchboard between your product and every AI model.
2. Routing picks the best-scoring healthy expert.
3. Circuit breakers stop redialling dead phones; hedges + fallbacks cover bad days.
4. Token buckets cap speed politely.
5. A semantic cache answers repeats for free.
6. The cost autopilot downgrades easy tasks before budgets break.
7. Flags + kill switch make every change reversible.
8. Signed tokens identify tenants; the ledger remembers every cent; GDPR forgets on request.
9. 106 offline tests prove all of it.
Final card: **AEGISGATE — the traffic controller for AI** with "106 tests · 0
errors at 484 rps" underneath.
Sequential/interaction: ticks land one by one.
Audio intent: wrap-up warmth. Audio-coupled: tick per line.
Music: bed continues, fades out over final card. Transition: fade to board.

**Music mood for this video:** calm/clean (vol-12), intro + payoff only.
**Audio summary:** quiet steady bed under the intro, voice alone through the
lecture body with sparse marker-tap accents, bed returns for the numbers and
the recap and fades on the final card.

## Voiceover script

Voice: Kokoro `af_heart`, one WAV per scene (`scene01.wav` … `scene11.wav`),
each scene's duration flexes to its WAV.

**Scene 1.** Imagine your product calls AI models the way a call centre phones
out to experts. You ring one expert for every question. But what happens when
that expert is sick? When they're overwhelmed? When their bill triples
overnight and nobody can explain why? That is the real, everyday problem
AegisGate solves. It is a gateway — one program that sits between your
application and a dozen AI models, and makes them behave like one reliable,
budgeted service.

**Scene 2.** Every request your product makes walks through this one door. And
as it passes through, the gateway does the boring-but-critical jobs
automatically: it checks who you are, limits how fast you're going, remembers
past answers, watches the budget, and picks the best expert available. Let's
walk one request through the switchboard, stage by stage.

**Scene 3.** Stage one: routing. A model registry is the roster — twelve models
from nine providers, each with a price and a quality tier. For every request,
the router computes a routing score: a blend of latency, error rate, cost, and
quality, weighted toward what this request cares about. Recent performance is
smoothed with an EWMA — a moving average that trusts recent events, so one
hiccup doesn't cause panic, but a real slump shows up fast. Lowest blended
score wins.

**Scene 4.** Now — what if the chosen expert doesn't answer? Three safety nets.
A circuit breaker watches each provider: after five consecutive failures it
trips open, and we simply stop redialling a dead phone — for thirty seconds.
Then the breaker half-opens, letting two probe calls through. If they succeed,
full service resumes. While waiting, failed calls retry with jitter: a random
delay, so thousands of clients don't all redial at once — the thundering herd.
If the primary is merely slow, not dead, the gateway hedges: it phones a second
model in parallel and takes whoever answers first, cancelling the loser. And if
everything fails, a fallback chain tries the next model on the list, with a
strict attempt budget. The user never sees any of this.

**Scene 5.** Stage two: rate limiting. Every tenant gets a token bucket —
literally a bucket of tokens that refills at a fixed rate. Each request spends
one token. Empty bucket? The gateway answers with HTTP 429 — "too many
requests" — plus a Retry-After header, telling the polite client when to come
back. Bursts are allowed up to the bucket's size, but nobody can exceed the
long-run refill rate. And with the Redis store, many gateway replicas share one
set of buckets.

**Scene 6.** Stage three: the semantic cache. Have I answered this before?
Tier one checks an exact fingerprint — a SHA-256 hash of the request. Tier two
is smarter: it turns the question into an embedding — a list of numbers where
similar meanings sit close together — and compares them with cosine similarity.
Score above 0.92, and the stored answer returns instantly, for free. Risky
requests — high temperature, tool calls — skip the cache entirely. Old entries
expire by TTL; the full shelf evicts the least recently used.

**Scene 7.** Stage four: money. The cost autopilot is a smart electricity meter
for AI. Every tenant has soft and hard budgets — the warning line, and the
wall. The meter projects month-end burn from spending so far. Approaching
eighty percent of the hard budget? The autopilot downgrades: easy tasks get
routed to cheaper models, by lowering the allowed quality tier. And every
request lands in an append-only ledger — a receipt book you only ever write in,
backed by write-ahead logging, so spend survives restarts.

**Scene 8.** Stage five: safety during change. New models don't go to everyone
at once. A feature flag rolls them out to five percent of users first, using
hash bucketing — hash the user's id, and it decides their bucket,
deterministically. The assignment is sticky, so nobody's app changes
personality mid-chat. Above it all sits a kill switch — one flag that removes a
model everywhere, instantly. And shadow mode is the dress rehearsal: the new
model mirrors real traffic, records how it would have answered — and never
serves a user.

**Scene 9.** Who are you, anyway? A bearer token — the wristband proving you
paid. AegisGate supports signed JWT badges — HS256 signatures over claims like
tenant and expiry — and stores static tokens only as SHA-256 fingerprints,
compared in constant time, so attackers can't time-sniff them. Rotation lets
the old token work during the switchover. And when a tenant asks to be
forgotten, GDPR erasure removes their rows in one audited operation.

**Scene 10.** Does it hold up? Here is the measured load test: fifty concurrent
users hammering the real gateway. Two thousand, nine hundred twenty-six
requests. Zero errors. Four hundred eighty-four requests per second. Now,
percentiles. Think of a queue at a coffee shop. p50 is the median — the typical
customer waited about thirty-six milliseconds. p95 means ninety-five out of a
hundred finished within sixty milliseconds. p99 — even the unlucky one percent
waited only sixty-eight. The tail barely rises above the median. That's what
the breakers, hedges, and cache are for: not the average case — the bad day.

**Scene 11.** Thirty-second recap. AegisGate is the switchboard between your
product and every AI model. It routes each request to the best-scoring healthy
expert. Circuit breakers stop redialling dead phones; hedges and fallback
chains cover the bad days. Token buckets cap speed politely. A semantic cache
answers repeats for free. The cost autopilot downgrades easy tasks before
budgets break. Flags and a kill switch make changes reversible. Signed tokens
identify tenants; an append-only ledger remembers every cent; GDPR erasure
forgets on request. One hundred six offline tests prove it — and at four
hundred eighty-four requests per second, zero errors. That's the traffic
controller for AI.

## Caption reference

The glossary terms that appear in the video are exported to
`brag-output/video-glossary.md` (term → on-screen definition → GLOSSARY.md
section) so captions and the repo glossary stay in sync.
