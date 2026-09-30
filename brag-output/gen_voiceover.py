"""Generate per-scene voiceover WAVs via `npx hyperframes tts` and record durations."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

NPX = shutil.which("npx") or "npx.cmd"

OUT = Path(__file__).parent / "composition" / "assets" / "voiceover"
OUT.mkdir(parents=True, exist_ok=True)

SCENES = {
    "scene01": "Imagine your product calls AI models the way a call centre phones out to experts. You ring one expert for every question. But what happens when that expert is sick? When they're overwhelmed? When their bill triples overnight, and nobody can explain why? That is the real, everyday problem AegisGate solves. It is a gateway: one program that sits between your application and a dozen AI models, and makes them behave like one reliable, budgeted service.",
    "scene02": "Every request your product makes walks through this one door. And as it passes through, the gateway does the boring but critical jobs automatically. It checks who you are. It limits how fast you're going. It remembers past answers. It watches the budget. And it picks the best expert available. Let's walk one request through the switchboard, stage by stage.",
    "scene03": "Stage one: routing. A model registry is the roster. Twelve models from nine providers, each with a price and a quality tier. For every request, the router computes a routing score: a blend of latency, error rate, cost, and quality, weighted toward what this request cares about. Recent performance is smoothed with an EWMA: a moving average that trusts recent events, so one hiccup doesn't cause panic, but a real slump shows up fast. Lowest blended score wins.",
    "scene04": "Now, what if the chosen expert doesn't answer? Three safety nets. A circuit breaker watches each provider. After five consecutive failures it trips open, and we simply stop redialling a dead phone, for thirty seconds. Then the breaker half opens, letting two probe calls through. If they succeed, full service resumes. While waiting, failed calls retry with jitter: a random delay, so thousands of clients don't all redial at once. That synchronized flood is called the thundering herd. If the primary is merely slow, not dead, the gateway hedges: it phones a second model in parallel, and takes whoever answers first, cancelling the loser. And if everything fails, a fallback chain tries the next model on the list, with a strict attempt budget. The user never sees any of this.",
    "scene05": "Stage two: rate limiting. Every tenant gets a token bucket. Literally a bucket of tokens that refills at a fixed rate. Each request spends one token. Empty bucket? The gateway answers with HTTP 429, too many requests, plus a Retry After header, telling the polite client when to come back. Bursts are allowed up to the bucket's size, but nobody can exceed the long run refill rate. And with the Redis store, many gateway replicas share one set of buckets.",
    "scene06": "Stage three: the semantic cache. Have I answered this before? Tier one checks an exact fingerprint, a SHA 256 hash of the request. Tier two is smarter. It turns the question into an embedding, a list of numbers where similar meanings sit close together, and compares them with cosine similarity. Score above 0.92, and the stored answer returns instantly, for free. Risky requests, like high temperature or tool calls, skip the cache entirely. Old entries expire by TTL. And the full shelf evicts the least recently used.",
    "scene07": "Stage four: money. The cost autopilot is a smart electricity meter for AI. Every tenant has soft and hard budgets: the warning line, and the wall. The meter projects month end burn from spending so far. Approaching eighty percent of the hard budget? The autopilot downgrades: easy tasks get routed to cheaper models, by lowering the allowed quality tier. And every request lands in an append only ledger, a receipt book you only ever write in, backed by write ahead logging, so spend survives restarts.",
    "scene08": "Stage five: safety during change. New models don't go to everyone at once. A feature flag rolls them out to five percent of users first, using hash bucketing. Hash the user's id, and it decides their bucket, deterministically. The assignment is sticky, so nobody's app changes personality mid chat. Above it all sits a kill switch: one flag that removes a model everywhere, instantly. And shadow mode is the dress rehearsal. The new model mirrors real traffic, records how it would have answered, and never serves a user.",
    "scene09": "Who are you, anyway? A bearer token: the wristband proving you paid. AegisGate supports signed JWT badges, HS 256 signatures over claims like tenant and expiry. It stores static tokens only as SHA 256 fingerprints, compared in constant time, so attackers can't time sniff them. Rotation lets the old token work during the switchover. And when a tenant asks to be forgotten, GDPR erasure removes their rows in one audited operation.",
    "scene10": "Does it hold up? Here is the measured load test. Fifty concurrent users, hammering the real gateway. Two thousand, nine hundred twenty six requests. Zero errors. Four hundred eighty four requests per second. Now, percentiles. Think of a queue at a coffee shop. P fifty is the median: the typical customer waited about thirty six milliseconds. P ninety five means ninety five out of a hundred finished within sixty milliseconds. P ninety nine? Even the unlucky one percent waited only sixty eight. The tail barely rises above the median. And that is what the breakers, hedges, and cache are for. Not the average case. The bad day.",
    "scene11": "Thirty second recap. AegisGate is the switchboard between your product and every AI model. It routes each request to the best scoring healthy expert. Circuit breakers stop redialling dead phones. Hedges and fallback chains cover the bad days. Token buckets cap speed politely. A semantic cache answers repeats for free. The cost autopilot downgrades easy tasks before budgets break. Flags and a kill switch make changes reversible. Signed tokens identify tenants. An append only ledger remembers every cent. And GDPR erasure forgets on request. One hundred six offline tests prove it. And at four hundred eighty four requests per second, zero errors. That's the traffic controller for AI.",
}

def wav_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True)
    return float(r.stdout.strip())

def main() -> int:
    only = sys.argv[1:] or list(SCENES)
    durations = {}
    dur_file = Path(__file__).parent / "voiceover-durations.json"
    if dur_file.exists():
        durations = json.loads(dur_file.read_text())
    for name in only:
        wav = OUT / f"{name}.wav"
        if wav.exists():
            durations[name] = wav_duration(wav)
            print(f"[skip] {name} exists, {durations[name]:.2f}s", flush=True)
            continue
        print(f"[tts ] {name} ...", flush=True)
        subprocess.run(
            [NPX, "hyperframes", "tts", SCENES[name],
             "--voice", "af_heart", "--output", str(wav)],
            check=True, capture_output=True, text=True)
        durations[name] = wav_duration(wav)
        print(f"       -> {durations[name]:.2f}s", flush=True)
    dur_file.write_text(json.dumps(durations, indent=2))
    total = sum(durations.values())
    print(f"TOTAL narration: {total:.1f}s ({total/60:.1f} min)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
