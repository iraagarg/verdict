# Verdict

[![CI](https://github.com/iraagarg/verdict/actions/workflows/ci.yml/badge.svg)](https://github.com/iraagarg/verdict/actions/workflows/ci.yml)

An OpenAI-compatible LLM gateway that records real traffic, replays it against candidate models,
judges quality with a human-calibrated evaluator, decides regression-vs-noise with proper
statistics, and uses that measurement to route live traffic to the cheapest model that still clears
a statistical quality floor.

> Existing tools (LangSmith, Braintrust, Promptfoo) measure LLM quality but do not close the loop to
> automatic cost-optimal routing with a proven quality guarantee. Verdict does.

---

## Architecture

```mermaid
flowchart LR
    App["Your app<br/><i>OpenAI SDK, baseURL swapped</i>"] -->|POST /v1/chat/completions| GW

    subgraph GW["apps/gateway · TypeScript + Fastify"]
        direction TB
        R["Router<br/><i>ambiguity → safe_default</i>"]
        C["Cost meter<br/><i>integer nano-USD</i>"]
        B["Circuit breaker<br/><i>per provider</i>"]
        Q["Trace queue<br/><i>write-behind, bounded</i>"]
        R --> C --> B
        C -.-> Q
    end

    B -->|SSE| P1["Anthropic"]
    B -->|SSE| P2["OpenAI"]
    B -->|SSE| P3["Groq"]
    Q --> PG[("Postgres<br/>+ pgvector")]

    PG -->|replay through the gateway| EV

    subgraph EV["apps/evald · Python · CLI, not a service"]
        direction TB
        RUN["Replay runner<br/><i>cached, capped, resumable</i>"]
        J["LLM judge<br/><i>position-swapped</i>"]
        ST["Statistics<br/><i>paired bootstrap + McNemar</i>"]
        RUN --> J --> ST
    end

    ST -->|"committed JSON"| ART[("artifacts/")]
    ART --> DASH["apps/dashboard<br/><i>Next.js, static</i>"]
    ART -.->|"policy.json, when fitted"| R
    ART --> GH["apps/ghapp<br/><i>PR comment bot</i>"]
```

The loop is the product: traffic is recorded, replayed, judged, tested for significance, and the
verdict becomes the routing policy. Every arrow into `artifacts/` is a committed file; every arrow
out is something reading one. Nothing reads a number from anywhere else.

---

## The result worth reading first

A pilot across three model tiers produced this:

| Model                | Correct on gradable slice |
| -------------------- | ------------------------- |
| `openai/gpt-oss-20b` | 63.3%                     |
| `claude-haiku-4-5`   | **81.7%**                 |
| `claude-sonnet-5`    | **81.7%**                 |

Haiku and Sonnet scored **identically**. Sonnet costs 2× more per token. The obvious conclusion is
"route everything to Haiku and halve the bill."

Verdict refuses to draw it:

```
VERDICT: INCONCLUSIVE
  claude-haiku-4-5 -> claude-sonnet-5
  effect        +0.0000  [-0.1000, +0.1000]  (95% CI)
  rates         0.8167 -> 0.8167
  n             60 items, 8 discordant
  margin        +/-0.0300
  resolution    0.1000   (too wide to ever show EQUIVALENT at this margin)
  McNemar p     1.000000  (exact, 8 discordant)
```

A point estimate of exactly zero and a p-value of exactly 1.0 — and the honest answer is still
**we cannot tell**. Sixty items resolve to ±10 percentage points, so a genuine 8-point gap would
look identical to what was observed. Separating these two rungs needs roughly 667 paired items.

This is the distinction the system exists to enforce. "No significant difference" conflates two
opposite findings, and only one of them justifies spending less:

|                                       |                |
| ------------------------------------- | -------------- |
| We measured precisely; it is small    | `EQUIVALENT`   |
| We could not measure precisely at all | `INCONCLUSIVE` |

It is not merely conservative. Given a real gap, at the same sample size, it commits:

```
VERDICT: IMPROVEMENT
  openai/gpt-oss-20b -> claude-haiku-4-5
  effect        +0.1607  [+0.0536, +0.2857]  (95% CI)
  McNemar p     0.022461  (exact, 13 discordant)
```

Both verdicts are committed under [`artifacts/`](artifacts/) with the git SHA, seed, corpus hash and
sample size that produced them.

---

## Status: what is measured and what is not

Measured numbers are rare and expensive, so this section is explicit about which is which.

**Measured, and in a committed artifact:**

- Pilot pass rates across three tiers, 60 gradable items —
  [`difficulty-pilot.json`](artifacts/difficulty-pilot.json)
- Two paired statistical verdicts — [`artifacts/verdict-*.json`](artifacts/)
- Semantic cache calibration, 200 paraphrases against 600 hard negatives —
  [`cache-calibration.json`](artifacts/cache-calibration.json)
- Total spend to produce all of it: **$0.49** (the cache calibration cost nothing — local
  embeddings, paraphrases on a free tier)

**Built, tested, and not yet run against real data:**

- The LLM judge and its calibration harness (needs ~200 hand labels)
- The routing policy fit (needs judged replay runs)
- The semantic cache's _live_ path. It is calibrated and the answer was "do not deploy", so the
  gateway serves no semantic cache.

**Not claimed:** any cost saving. The routing machinery works and is tested end to end, but the
corpus has not been replayed at the scale needed to fit a policy worth deploying. A savings figure
without that would be exactly the kind of number this project is built to refuse.

Rule enforced throughout: **a number that is not in a committed artifact does not exist.** Nothing
in this README was typed by hand; every figure is read from `artifacts/`.

---

## What each piece does

| Component         | Role                                                                                                                                                         |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `apps/gateway`    | TypeScript + Fastify. OpenAI-compatible proxy, SSE streaming, three provider adapters, exact cost accounting, circuit breaker, routing.                      |
| `apps/evald`      | Python + FastAPI. Corpus assembly, replay runner, LLM judge, calibration, statistics, policy fitting.                                                        |
| `apps/dashboard`  | Next.js. Pareto curve, spend by model, run comparison with the verdict drawn to scale, trace explorer with streaming replay. Reads committed artifacts only. |
| `packages/shared` | Shared TypeScript types and Zod schemas.                                                                                                                     |
| `config/`         | Model ladder and pricing. Every price cites its source and the date verified.                                                                                |
| `corpus/`         | 1,500 frozen benchmark items with per-item provenance and licence.                                                                                           |
| `artifacts/`      | Committed measurements. The only source of any number.                                                                                                       |

### Design decisions worth a look

[`DECISIONS.md`](DECISIONS.md) records 56 decisions with their rejected alternatives. The ones that
changed the project most:

- **[D-034](DECISIONS.md)** — why a paired bootstrap rather than a t-test, in plain language
- **[D-035](DECISIONS.md)** — why three verdicts became four
- **[D-030](DECISIONS.md)** — a pilot measured `gpt-oss-120b` at **98% on GSM8K**, so half the
  gradable corpus could not distinguish any two models. Replaced with harder maths; re-measured at
  30%.
- **[D-027](DECISIONS.md)** — rate limits were opening the circuit breaker, turning 7 HTTP 429s into
  339 refused requests. A 429 means healthy-but-throttled, not broken.
- **[D-020](DECISIONS.md)** — client-disconnect detection on the request stream fires when the
  request _body_ finishes reading, so every streaming request looked like an instant disconnect.
  Every mocked test passed; only a real socket caught it.

---

## Running it

New here? Two guides, both hands-on:

- [`START-HERE.md`](START-HERE.md) — six commands, ten minutes, plain words. The
  whole idea of the project end to end.
- [`WALKTHROUGH.md`](WALKTHROUGH.md) — thirteen labs taking the system apart while
  it runs: streaming, cost arithmetic, client disconnect, the circuit breaker, the
  four verdicts, the rejected cache.

Both are free to run except one request costing $0.0002.

### One command, from a clean clone

```bash
make up
```

That is the whole thing: it writes `.env` from the example if you have none, starts Postgres,
applies migrations as a one-shot service that must exit 0, then brings up the gateway, evald and the
dashboard — and waits until each reports healthy before returning. CI runs exactly this from a clean
clone on every push, which is why the instruction can be trusted.

Add a provider key to `.env` when you want to call a real model. Without one the stack still comes
up and the dashboard still shows every committed measurement; only live requests need a key.

The gateway is a drop-in for the OpenAI API — change `baseURL` and nothing else:

```bash
curl -N http://localhost:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"claude-haiku-4-5","messages":[{"role":"user","content":"Explain SSE in one sentence."}],"stream":true}'
```

Response headers report what Verdict did, leaving the body a byte-for-byte OpenAI shape:
`x-verdict-model-served`, `x-verdict-route`, `x-verdict-route-reason`, `x-verdict-cost-usd`,
`x-verdict-usage-final`.

The dashboard is on [localhost:3000](http://localhost:3000): measured verdicts, spend by model,
the cache calibration, and a trace explorer that replays a recorded stream.

### Benchmarking

```bash
make corpus                 # rebuild the corpus from public datasets (free)
make plan CAP=30            # projected cost. Spends nothing.
make pilot CAP=1 N=200      # measure whether the corpus discriminates
make bench CAP=30           # full replay; writes a versioned artifact
make label PAIRS=200        # hand-label pairs for judge calibration (free)
make calibrate PAIRS=200    # judge them; writes calibration_report.json
make fit                    # fit the routing policy
```

```bash
cd apps/evald && .venv/bin/python -m evald.cli cache-calibrate   # free: local embeddings
```

Every command that can spend money prints a projection and refuses to start without a cap. Results
are written to a content-addressed cache, so a second run of an unchanged corpus makes **zero paid
calls**.

### Routing

Off by default; must be enabled deliberately:

```bash
ROUTER_MODE=offline POLICY_PATH=artifacts/policy.json docker compose up -d gateway
```

Send `model: "verdict-auto"` with an `x-verdict-route` header. Every ambiguous case — router off, no
policy, no route hint, an unknown route — serves the strong `safe_default` instead. Cost
optimisation happens only where there is positive evidence it is safe.

---

## Performance: what the gateway costs you

Measured by [`make loadtest`](Makefile), committed to
[`artifacts/loadtest.json`](artifacts/loadtest.json).

|     | baseline | through gateway | added     |
| --- | -------- | --------------- | --------- |
| p50 | 18 ms    | 18 ms           | **+0 ms** |
| p95 | 20 ms    | 23 ms           | **+3 ms** |
| p99 | 20 ms    | 26 ms           | **+6 ms** |

**Sustained 158,790 req/min** — 52,925 requests, **0 non-2xx, 0 errors**. 50 connections, 20s,
Apple arm64, 10 cores, Node v26.5.0.

For scale: the same gateway against **real Groq** measured **p50 795 ms, p95 1231 ms**. The gateway
is roughly **0.1% of a real call**. That is the number worth knowing — not because 1 ms is
impressive, but because it settles whether putting a measurement layer in the request path costs
anything a user would notice. It does not.

### Why the upstream is a mock

The gateway's contribution is about a millisecond. A provider's own latency varies by hundreds of
milliseconds between identical calls — the Groq run above spans 436 ms from p50 to p95 on one
prompt. Measuring overhead against a real provider would measure the provider: the signal sits three
orders of magnitude below the noise, and the result would be indistinguishable from a slow afternoon
at Anthropic.

So both arms run against the same controlled upstream — the mock the correctness tests use, real
Anthropic SSE over a real socket — with identical load, back to back, on one machine. The difference
is the gateway and nothing else. Trace persistence is **on**, against a real Postgres, because it is
part of what the gateway costs.

Three things this table does not claim:

- **`+6 ms` is a difference of percentiles, not the 99th percentile of added latency.** The slowest
  1% of each arm need not be the same requests. Both raw distributions are in the artifact so the
  subtraction can be checked.
- **The gateway arm reads 1.33× the bytes**, because OpenAI's chunk envelope is more verbose than
  Anthropic's and translating between them is the gateway's job. That extra work lands on the arm
  being measured, so the true cost is **at most** this, never more.
- **One machine, no network.** A real deployment adds latency that dwarfs all of it.

The first version of this test got it wrong and said so loudly: it streamed on one arm and not the
other, and reported the gateway as **faster than not having a gateway**. The runner now exits
non-zero on a negative p50 rather than publishing an impossibility ([D-057](DECISIONS.md)).

---

## Development

```bash
make install      # pnpm workspace + evald virtualenv (Python 3.12)
make check        # lint + typecheck + test, in CI's order. Run before pushing.

make test         # 254 TypeScript tests, 413 Python tests
make lint         # eslint, prettier, ruff
make typecheck    # tsc --strict, mypy --strict
make fmt          # auto-fix formatting
```

`make check` exists because CI's TypeScript job runs `prettier --check .`, which
covers markdown as well as code — so a documentation-only commit can turn CI red
without touching a line of TypeScript.

CI runs four jobs on every push: TypeScript (lint, typecheck, test), Python (ruff, mypy, pytest),
database migrations applied twice to prove idempotency, and a full `docker compose up` from a clean
clone with health checks. The last one is why the quick-start above can be trusted.

TypeScript strict with no `any`; Zod at every boundary. Python with `mypy --strict`; Pydantic at
every boundary. Secrets validated at boot — the process exits before binding a port rather than
failing at request time.

The statistics are implemented from first principles and tested against `scipy` as an independent
oracle, plus against synthetic data with a planted effect. The second kind catches wiring errors
(wrong arms compared, pairing lost, sign flipped) that agreeing with scipy cannot.

---

## The semantic cache: measured, and rejected

A near-duplicate cache reuses a previous answer when a new request means the same thing. It was
built, calibrated against 200 paraphrase pairs and 600 hard negatives, and **the measurement says
not to deploy it**.

| Threshold | Hit rate | False-hit rate | Upper bound | Safe? |
| --------- | -------- | -------------- | ----------- | ----- |
| 0.850     | 51.0%    | 14.17%         | 17.22%      | no    |
| 0.900     | 32.0%    | 3.33%          | 5.10%       | no    |
| 0.950     | 12.0%    | 1.33%          | 2.61%       | no    |
| 0.975     | 1.0%     | 0.33%          | 1.20%       | no    |

At a 1% tolerance, no threshold is both safe and useful. Rather than stop at "no", the calibration
prices the alternative:

| Accept up to | Best hit rate | at threshold |
| ------------ | ------------- | ------------ |
| 1% wrong     | —             | none exists  |
| 2% wrong     | 8.0%          | 0.955        |
| 5% wrong     | 28.0%         | 0.910        |
| 10% wrong    | 43.5%         | 0.870        |

**Why it fails here is the interesting part.** This corpus is academic questions, and within a topic
they are lexically near-identical while being semantically distinct. The hardest negative pair
found:

```
How many ways are there to put 4 distinguishable balls into 2 indistinguishable boxes?
How many ways are there to put 4 indistinguishable balls into 2 distinguishable boxes?
```

Two words swapped, different answers, cosine similarity **0.9986**. No threshold separates that from
a genuine paraphrase, and serving one answer for the other is exactly the failure a cache must never
make. A workload of distinct support tickets would likely calibrate very differently — the method
transfers, this particular verdict does not.

Two things that fell out of building it honestly:

**The bootstrap is the wrong tool for a rare event.** Every other rate in this project uses a
percentile bootstrap (D-011). Resample zero false hits out of 150 any number of times and every
resample still contains zero, so the interval is `[0, 0]` — and the first calibration duly reported
"0.00% false hits, upper bound 0.00%" for a threshold that had simply not been tested hard enough.
Since the threshold is chosen _on that upper bound_, the cache would have been loosened on an
artefact of the method. The false-hit rate now uses a Clopper-Pearson interval, verified against
`scipy`: 0 of 150 is **2.43%**, not 0%.

**A safety bound has a floor set by sample size, not by results.** Proving a false-hit rate under 1%
needs at least **368 hard negatives even with zero observed false hits**. Below that, "0%" means
untested. The calibration says so and refuses to pick a threshold rather than reporting a flattering
number.

Negatives are hard by construction — for each item, its nearest _different_ neighbour in embedding
space. Random negatives from a 1,500-item corpus are trivially separable, so any threshold would
look excellent while saying nothing about production.

## Limitations

The things that would break this if you took it seriously, in roughly the order they would bite.

**The headline comparison is underpowered.** Haiku vs Sonnet was run on 60 items, which resolves to
±10 percentage points. That is why it returns `INCONCLUSIVE` rather than `EQUIVALENT`, and it means
the one comparison a reader most wants — _is the cheaper model good enough?_ — is unanswered.
Answering it needs roughly 667 paired items.

**The judge has never been calibrated.** The pairwise judge and its labelling harness are built and
tested; no human has labelled anything with them, so there is no Cohen's kappa and no evidence the
judge agrees with a person. Every verdict in `artifacts/` therefore uses **exact-match verifiers on
the gradable slice only**. The free-form slice — 300 items, the half that actually resembles
production traffic — has produced no measurement at all.

**No routing policy has been fitted, so no cost saving is claimed.** The router, the cascade, the
sweep and the held-out evaluation all work and are tested end to end. None has been run on a full
judged replay, which costs roughly $59 at current prices. Until then `safe_default` serves
everything and Verdict saves nothing.

**The corpus is academic questions, and that shapes every conclusion.** Most visibly the cache: this
corpus is lexically near-identical within a topic while semantically distinct, which is the worst
case for embedding similarity. A workload of distinct support tickets would very likely calibrate
differently. The method transfers; that particular verdict does not.

**Load figures are single-machine.** Client, gateway and upstream on one host, no network between
them. They bound the gateway's own cost; they say nothing about it behind a real load balancer, and
a free-tier Neon that sleeps when idle will dominate the first request after a quiet period.

**Single-turn only, and no per-request guarantee.** Multi-turn conversations, tool calls and vision
are out of scope. The guarantee is distributional over a route, never about any individual response
— see the non-goals below.

**Prices are pinned, not fetched.** Every price in `config/models.yaml` carries `verified_at` and a
source URL, and the schema rejects one without them. It does not stop them going stale. A provider
price change silently makes every historic cost figure wrong until someone re-verifies.

**One person, one reviewer.** No production traffic has ever hit this. Every design decision in
`DECISIONS.md` is argued, but none has been contradicted by an incident.

---

## Deliberate non-goals

- Not a training or fine-tuning platform.
- Not a general observability backend.
- No multi-tenant auth, RBAC or billing.
- v1 evaluates single-turn completions only.
- **No per-request quality guarantee.** The guarantee is distributional over a route: "on traffic
  resembling this route, the cheap model's win-or-tie rate against the reference is at least F, with
  95% confidence." A single bad response is fully consistent with that holding. Any stronger claim
  would be false.
