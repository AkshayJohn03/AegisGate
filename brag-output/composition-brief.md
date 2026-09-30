# Hyperframes Composition Brief: AegisGate — whiteboard lecture

## Objective

Create a long-form whiteboard explainer lecture (NOT a launch video) that
teaches AegisGate to its own owner: call-centre analogy → routing/breaker/
fallback/hedging → rate limiting → semantic cache → cost autopilot → flags/
kill switch → JWT + ledger + GDPR → measured load numbers with percentiles
explained → 30-second recap. Narration on; every keyword defined on screen the
moment it first appears.

## Output

- Composition directory: `brag-output/composition/`
- Rendered video: `brag-output/brag.mp4`
- Format: landscape — 1920x1080
- Duration: ~397.8s (6 min 37s), voice-paced. This overrides brag's 15–25s
  default per TUTOR_BRIEF.md.

## Source Material

- Project root: `D:\aria\Projects\AegisGate`
- Primary files read: README.md, docs/RUNBOOK.md, docs/load_profile.json,
  scripts/load_profile.py, src/aegisgate/auth.py, src/aegisgate/meter_store.py,
  GLOSSARY.md (written this run)
- Product name: AegisGate
- Tagline / strongest claim: "the traffic controller for AI"; measured:
  106 tests, 2,926 requests, 0 errors, 484 rps, p50 ~36ms / p95 ~60ms / p99 ~68ms
- Key UI/visual moment to recreate: the real quickstart request —
  `curl -s localhost:8000/v1/chat/completions -H "Authorization: Bearer sk-demo"`
  and a JSON response containing `"served_by"` (README.md Quickstart).
- Copy that must appear verbatim:
  - `POST /v1/chat/completions`
  - `Authorization: Bearer sk-demo`
  - `"served_by"` (highlighted in the response card)
  - `429` · `Retry-After` · `cosine ≥ 0.92` · `106 tests` · `2,926` · `0 errors` · `484 rps`

## Creative Direction

- Tone preset: `polished` + freeform "patient senior engineer at a whiteboard"
- Interpretation: long holds, handwriting-speed reveals, no rapid cuts, muted
  warm palette, sticky-note definition cards that stay put. Calm and precise.
- Angle: one request walks through the switchboard stage by stage; every
  keyword gets a one-sentence sticky note at first use (series rule).
- Hook: a row of ringing phones calling ONE expert desk; the expert gets a red
  SICK cross while the phones keep ringing.
- Outro: nine-tick recap checklist landing on the final card
  "AEGISGATE — the traffic controller for AI".
- Avoid: hype adjectives ("lightning-fast", "supercharge"), launch energy,
  abstract filler, waveform/equalizer visuals, rapid scene cuts.

## Visual Identity

- Background: `#F8F5EE` warm whiteboard white, subtle `#E8E3D6` grid
- Text/ink: `#22303F`
- Accents: marker blue `#2E6FB7`, marker red `#C43D3D`, marker green `#3D8B5F`,
  highlighter yellow `#F2C94C` (as background highlight with dark ink)
- Display font: "Segoe Print" via in-file `@font-face` to local
  `assets/fonts/segoepr.ttf` (+ `segoeprb.ttf` bold) — marker handwriting feel
- Body font: "Segoe UI" (system; fallback system-ui) — used for the terminal
  card only; all lecture text uses Segoe Print
- Visual references: hand-drawn chalk boxes (irregular border-radius, slight
  rotation), SVG arrows with hand-drawn dash, sticky-note definition cards
  with pin dot, tally marks, stamps.

## Storyboard

Use `brag-output/brag-plan.md` as the creative contract — 11 scenes, absolute
times (seconds):

1. The problem (call centre) — 0–29.7 — phones ring one expert; SICK stamp; API-gateway sticky
2. One request through the switchboard — 29.7–52.9 — 6 pipeline boxes one by one; tenant sticky; real curl + JSON response with `served_by`
3. Routing — 52.9–81.4 — score equation with 4 weight chips; model-registry/routing-score/EWMA stickies; EWMA graph draws
4. Breaker, retry, hedge, fallback — 81.4–129.9 — 2x2 mini-boards: breaker states with tallies+probe, jitter arrows, hedge race, fallback list; 5 stickies
5. Token bucket — 129.9–162.8 — bucket drips tokens; request spends one; 429 stamp + Retry-After; Redis note
6. Semantic cache — 162.8–201.2 — tier 1 SHA-256 fingerprint; tier 2 embedding vectors + cosine ≥ 0.92; skip rules; TTL/LRU
7. Cost autopilot — 201.2–236.0 — meter; burn projection crosses 80% of hard budget; downgrade switch flips; append-only ledger rows; WAL
8. Flags — 236.0–271.3 — 100-dot grid, 5 highlight; hash bucketing; sticky; big red kill switch; shadow mirror stamped NEVER SERVES
9. Identity, receipts, erasure — 271.3–306.3 — bearer wristband; JWT badge with HS256 fields; SHA-256 fingerprints + constant-time compare; rotation; GDPR eraser
10. The measured numbers — 306.3–348.8 — 2,926 / 0 / 484 rps / 50 VU count up; coffee-shop queue explains p50 ~36ms / p95 ~60ms / p99 ~68ms
11. 30-second recap — 348.8–397.8 — 9 checklist ticks; final card AEGISGATE

## Audio

- Audio role: voice-first lecture; quiet steady music bed at intro and payoff.
- Audio arc: music fades in under scene 1, out by end of scene 3's start;
  voice alone (with sparse marker SFX) through the body; music returns for the
  numbers scene and recap; fades on the final card.
- Music: `assets/music/happy-beats-business-moves-vol-12-by-ende-dot-app.mp3`
  (1:58). Two placements (same file, non-overlapping): music-a at 0s
  (duration 55s), music-b at 306.3s (duration 91.5s). Volume via automation
  lanes (a: 0→0.14 by 2s, hold, →0 by 55s; b: 0→0.12 by 2.5s, hold, →0 by 91.5s).
- Music cue guidance: preset at
  `C:\Users\Akshay\.agents\skills\brag\assets\music\cues\happy-beats-business-moves-vol-12-by-ende-dot-app.music-cues.json`
  — lecture is voice-paced; use at most 1–2 strong-cue locks (final card land),
  natural timing everywhere else. Readability and narration win over beats.
- Audio-reactive treatment: none — a whiteboard lecture stays still and calm.
- Audio-coupled moments: pipeline box drops (scene 2), breaker trip (scene 4),
  429 stamp (scene 5), fingerprint stamp (scene 6), ledger ticks (scene 7),
  kill switch flip (scene 8), number counters (scene 10), checklist ticks
  (scene 11).
- SFX selection guidance: sparse palette only — impactWood_light (marker taps),
  interface/drop_001–002 (cards/stickies), interface/switch (breaker/kill
  switch), interface/error_005 (429), impactSoft_medium (numbers/final). Low
  volumes (0.35–0.6); never compete with narration.
- Exact SFX choice: Hyperframes finalizes after animation exists; copy chosen
  files into `composition/assets/sfx/…`.
- Voiceover: already generated, `assets/voiceover/scene01.wav` … `scene11.wav`
  (Kokoro af_heart). Durations recorded in `brag-output/voiceover-durations.json`.
  Voice elements on track 3 starting 0.5s after their scene's start; music on
  track 10; SFX on tracks 11+.

## Hyperframes Instructions

Load and follow the Hyperframes domain skills (`hyperframes-core`,
`hyperframes-animation`, `hyperframes-creative`, `hyperframes-keyframes`,
`hyperframes-cli`). /brag owns the content; Hyperframes owns implementation.

Requirements:
- Standalone monolithic composition: one `index.html`, root
  `data-composition-id="aegisgate-lecture"`, `data-width="1920"`,
  `data-height="1080"`, `data-duration="397.8"`.
- One paused GSAP timeline registered at `window.__timelines["aegisgate-lecture"]`,
  built synchronously; tweens positioned at absolute scene times.
- Never tween `.clip` elements (autoAlpha/visibility/display) — animate inner
  children. Use `tl.from(...)`/`fromTo` for entrances (immediateRender hides
  pre-entrance state). No CSS-transform + GSAP-transform conflicts.
- No `Math.random()` without a seed; finite repeats only; no network; fonts
  via `@font-face` to local files (`assets/fonts/segoepr.ttf`, `segoeprb.ttf`).
- All text ≥ 22px, ink `#22303F` on board `#F8F5EE`; colored accents only for
  large marker text/labels. Must pass `check`'s WCAG contrast pass.
- Voice drives pacing: keep each scene's reveals inside its window and hold
  the final state ~1s before the scene ends. Land animation ends slightly
  before scene boundaries.
- `npx hyperframes check` is the single gate before render — fix every error.
- Render to `../brag.mp4` (i.e. `brag-output/brag.mp4`).
