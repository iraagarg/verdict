# DECISIONS.md

Engineering decisions for Verdict, with the alternatives that were rejected and why.
This file is interview preparation: every entry should let me answer "why did you do it that way?"
without reconstructing the reasoning on the spot.

**Status legend** — `ACCEPTED`: decided by Iraa. `PROPOSED`: recommendation awaiting Iraa's call.
`FORCED`: external constraint left no real choice; recorded because the constraint itself is
interesting.

---

## D-001 — Benchmark corpus composition

**Status:** ACCEPTED · **Date:** 2026-09-13 · **Phase:** P0 · **Affects:** P2, P3, P5

**Decision.** ~300 items, ~60% machine-verifiable (extraction, classification, SQL) and ~40%
open-ended (summarization, support replies) over a fixed document set. Split
calibration 20% / dev 30% / test 50%, stratified by task type.

**Alternatives rejected.**

- _Single domain, open-ended only._ Tighter narrative and more realistic as "traffic", but the judge
  could then only ever be validated against my own ~180 labels. There would be no objective anchor,
  and the obvious interview question — "how do you know your judge is any good?" — would have only a
  circular answer.
- _Single domain, fully verifiable._ Rigorous and cheap to score, but self-defeating: if answers are
  programmatically checkable you do not need an LLM judge, which removes the project's reason to
  exist.
- _Public eval sets (MT-Bench, HELM subsets)._ Fast and comparable to published numbers, but not
  "real traffic", carries genuine contamination risk on frontier models, and reframes Verdict as a
  benchmark harness rather than a production gateway.

**Rationale.** The dual composition is what makes P3 produce two independent numbers instead of one:
judge-vs-ground-truth **accuracy** on the verifiable slice, and judge-vs-human **Cohen's κ** across
the whole corpus. The verifiable slice anchors the instrument against objective truth; the open-ended
slice is where a judge is genuinely necessary and where routing decisions are non-trivial. Neither
slice alone supports the claim.

**Consequence.** Corpus assembly in P2 is more work — two authoring pipelines and a verifier registry
instead of one path.

---

## D-002 — Evaluation budget

**Status:** ACCEPTED · **Date:** 2026-09-13 · **Phase:** P0 · **Affects:** P2–P6

**Decision.** $50–100 total for live model API calls across the project.

**Alternatives rejected.** _Under $20_ would force ~120 items, 3 rungs and 1 judge replicate,
producing visibly wide confidence intervals and undercutting the "statistically proven floor" claim.
_Free tiers only_ would restrict nearly every number to one cheap model, making most headline claims
unsupportable.

**Rationale.** Supports ~300 items × 4–5 rungs × 3 judge replicates with room to re-run after a
rubric revision. The content-addressed generation cache (D-009) means a repeat `make bench` costs $0,
so the budget is spent on genuinely new measurements rather than on CI re-runs.

**Consequence.** Offline work (replay in P2, judging in P3) should use the **Batch API at 50% cost**
— it is latency-insensitive by definition. This roughly doubles the effective budget and is the
single highest-leverage cost decision in the project.

---

## D-003 — Judge design

**Status:** ACCEPTED · **Date:** 2026-09-13 · **Phase:** P0 · **Affects:** P3, P4, P5

**Decision.** Pairwise comparison against a **frozen reference output** from the strong rung,
emitting `win`/`tie`/`loss`, judged in both A/B orders with disagreement collapsing to `tie`.

**Alternatives rejected.**

- _Pointwise rubric 1–5._ Cheapest and gives a directly interpretable absolute score. Rejected
  because LLM pointwise scores are poorly calibrated across items and drift with rubric wording; the
  likely outcome is a mediocre κ that I then have to defend.
- _Hybrid rubric + pairwise._ Most rigorous and allows cross-validating two signals, but roughly
  doubles judge cost and means hand-labelling for two separate instruments — out of budget (D-002)
  and out of time.
- _Reference-answer grading against human-written gold answers._ Highest agreement, very defensible,
  but writing gold answers for 200+ open-ended items is many hours and freezes the corpus hard.

**Rationale.** One mechanism serves three phases, which is far easier to defend than three
instruments. Pairwise verdicts give (a) paired binary outcomes, so **McNemar** falls out exactly in
P4; (b) a bootstrap CI on win-or-tie rate; and (c) an absolute-enough quality floor — "≥F win-or-tie
against the strong reference" — that P5's router can enforce. Ties are first-class because forcing a
binary verdict on equivalent outputs manufactures variance.

**Consequence.** Judge cost is 2× per comparison (both positions must be judged). Accepted: the flip
rate between positions becomes a _measured and reported_ bias metric rather than a silent one.
Self-preference risk (Opus judging an Opus reference) is a known threat to validity, mitigated by
cross-family re-judging on a held-out subset.

---

## D-004 — Routing policy

**Status:** ACCEPTED · **Date:** 2026-09-13 · **Phase:** P0 · **Affects:** P5

**Decision.** Offline per-route assignment. Cluster prompts into routes, and for each route select
the cheapest rung whose **lower confidence bound** clears the quality floor on a held-out split.
Runtime routing is an ANN lookup — no judge in the hot path.

**Alternatives rejected.**

- _Online cascade with escalation._ Adapts per request and handles novel prompts, but puts an extra
  model call in the latency path and reduces the "guarantee" to the verifier's accuracy — a
  materially weaker claim that an interviewer would probe hard.
- _Both (offline floor + opt-in cascade)._ Richest result, two comparable Pareto curves, but
  meaningfully more P5 work and double the evaluation surface.
- _Learned difficulty predictor._ Sounds impressive for an AI-engineer role, but with a few hundred
  items it would overfit, and a weak model is worse than no model.

**Rationale.** This is the only option where "proven quality guarantee" is literally true, because
the proof _is_ the held-out significance test. The decision rule uses `ci.low ≥ F` rather than the
point estimate, so small samples produce wide intervals and therefore refuse to demote — the method
is biased toward spending money, which is the correct direction for a safety mechanism.

**Consequence.** Novel traffic that does not resemble any route is not covered by any guarantee.
Handled structurally by the `TAU_ROUTE` out-of-distribution guard, which routes unrecognised prompts
to the strong rung.

---

## D-005 — GitHub App vs GitHub Action

**Status:** ACCEPTED · **Date:** 2026-09-13 · **Phase:** P0 · **Affects:** P7

**Decision.** `apps/ghapp` is dropped from the workspace. The PR integration is a **GitHub Action**
that runs the eval and posts the quality + cost diff table using the built-in `GITHUB_TOKEN`. The
App remains a stretch goal, not a deliverable.

**Alternatives.**

- _GitHub App (as briefed)._ Richer Checks API integration, works across repos without per-repo
  workflow files, and is the right answer for a real product. Costs: app registration, a publicly
  reachable webhook endpoint, webhook secret verification, delivery retries and idempotency, and a
  fourth deployable to keep alive.
- _Action._ ~80 lines, runs from a clean clone with no external infrastructure, and a reviewer can
  read the whole integration inside the PR.

**Rationale.** The webhook infrastructure demonstrates nothing that P1–P6 does not already
demonstrate more convincingly, while adding a permanently-running service that can break during a
demo. For a portfolio project defended in interviews, "I chose the Action because the App's extra
complexity bought no additional signal" is itself a good answer about scoping.

**Consequence.** The monorepo has three apps, not four: `gateway`, `evald`, `dashboard`. The PR
integration lives at `.github/workflows/verdict-eval.yml` plus a small `tools/pr-comment/` script.
The eval must therefore run from a clean clone with no long-lived service — a constraint worth having
anyway, since `make bench` needs the same property.

---

## D-006 — Semantic cache lives in pgvector, not Upstash Redis

**Status:** ACCEPTED · **Date:** 2026-09-13 · **Phase:** P0 · **Affects:** P6

**Decision.** Redis holds the **exact-match** cache (SHA-256 of the canonicalised request) and
rate-limit counters. **pgvector** holds the semantic cache index.

**Alternatives.**

- _Upstash Redis for both (as briefed)._ Rejected on a factual basis: Upstash Redis has no native
  vector/ANN search. Upstash **Vector** is a separate product, which would mean a third vendor.
- _Upstash Vector as a third datastore._ Purpose-built and would work, but adds a vendor, a second
  embedding-write path, and a consistency problem between the cache index and the traces it must be
  joined against.

**Rationale.** pgvector is already a hard dependency for routing. P6's headline requirement is
reporting the **false-hit rate** alongside the hit rate, and that analysis is a join between cache
hits and the traces/generations they should have matched — trivially a SQL query if both live in
Postgres, and an export-and-reconcile job if they do not.

**Consequence.** A semantic cache probe becomes a Postgres round-trip rather than a Redis one,
raising cold-path latency. This is measured in P6 and is the reason HNSW was chosen over IVFFlat
(D-008). If the latency proves unacceptable, the fallback is an in-process ANN index refreshed from
Postgres — recorded here so the escape hatch is not invented under pressure later.

---

## D-007 — Trace recording is write-behind, off the request path

**Status:** PROPOSED · **Date:** 2026-09-13 · **Phase:** P0 · **Affects:** P1

**Recommendation.** Traces are enqueued to a bounded in-process queue and flushed to Postgres in
batches by a background worker. On queue-full, drop the oldest and increment
`verdict_traces_dropped_total`.

**Alternatives.**

- _Synchronous insert before responding._ Zero trace loss, simplest to reason about. Rejected: it
  puts a database round-trip inside a proxy whose entire justification is that it is ~99% network
  wait, and it makes Postgres availability a hard dependency of serving availability.
- _External queue (SQS/Kafka/Redis Streams)._ Durable across process restarts and the right answer
  at scale. Rejected for now as another piece of infrastructure for a single-instance gateway;
  the interface is kept narrow enough to swap in later.
- _Unbounded in-memory queue._ Rejected outright — it converts a database outage into an OOM kill,
  turning a degradation into an incident.

**Rationale.** Verdict must state a clear preference when the analytics store fails, and the
defensible preference is **lose observability, keep availability**. Bounded-and-counted makes the
loss explicit and measurable rather than silent.

**Consequence.** Trace loss is possible during a Postgres outage. The dropped-trace counter is a
first-class metric and appears on the dashboard.

---

## D-008 — HNSW over IVFFlat for both vector indexes

**Status:** PROPOSED · **Date:** 2026-09-13 · **Phase:** P0 · **Affects:** P5, P6

**Recommendation.** HNSW for `routes.centroid` and `semantic_cache_entries.embedding`.

**Alternatives.** _IVFFlat_ builds faster and uses less memory, but requires a populated,
representative table at build time to produce meaningful centroids, and degrades as the data
distribution shifts — which is exactly what a filling cache does. It would need a rebuild schedule.
_No index (exact scan)_ is viable at a few thousand rows and is genuinely the right call early, but
both indexes are on the request hot path and I would rather not discover the cliff in production.

**Rationale.** Both lookups add latency to every routed request, so recall-at-latency is the metric
that matters and HNSW wins it. Build cost and memory are irrelevant at tens of thousands of rows.

---

## D-009 — "Deterministic replay" redefined at the pipeline level

**Status:** FORCED · **Date:** 2026-09-13 · **Phase:** P0 · **Affects:** P2, P4

**Constraint.** `temperature`, `top_p` and `top_k` are **removed on Claude Opus 5 and Claude
Sonnet 5** and return HTTP 400. Sample-level determinism via `temperature: 0` is not achievable on
the mid and strong rungs.

**Decision.** Determinism is defined as _pipeline_ determinism, not _sample_ determinism:
content-addressed generation cache keyed on `sha256(model ‖ params_hash ‖ messages ‖ replicate_idx)`;
one run seed driving corpus splits, k-means init, bootstrap resampling and A/B position assignment;
K replicates per (item, model) so sampling variance is **measured and carried into the CI**; full
provenance on every generation.

**Alternatives rejected.** _Restrict the ladder to models that still accept `temperature`._ Would
exclude Sonnet 5 and Opus 5 — i.e. the entire mid and strong tiers — and destroy the project.
_Claim determinism anyway._ Not an option under non-negotiable #1.

**Rationale.** This is a better position than `temperature: 0` would have been. It makes the
_conclusion_ reproducible while being honest that the _samples_ are not, and it turns sampling
variance from an unmeasured confound into a quantity that widens the confidence interval. A model
with erratic outputs therefore becomes harder to promote — the correct incentive.

---

## D-010 — Unsupported sampling parameters are dropped and reported, not rejected

**Status:** PROPOSED · **Date:** 2026-09-13 · **Phase:** P0 · **Affects:** P1

**Recommendation.** For auto-routed requests, the adapter drops sampling params the served rung does
not support, serves the request, and reports the drop via `x-verdict-dropped-params` and on the
trace. For requests that **explicitly pin** a model, an unsupported param is a `400`.

**Alternatives.**

- _Always 400._ Most honest, but breaks the drop-in promise: an unchanged OpenAI client sending
  `temperature: 0.7` would fail the moment the router picked Sonnet 5.
- _Always drop silently._ Preserves compatibility but silently changes semantics for a user who set
  `temperature: 0` expecting determinism. Unacceptable.

**Rationale.** The split respects intent. If the router chose the model, Verdict owns the
compatibility problem and must not fail the user's request — but it must say what it did. If the user
chose the model, the incompatibility is theirs to resolve and a silent drop would be misleading.

**Consequence.** Ladder rungs are not parameter-uniform (Haiku 4.5 accepts sampling params and
`budget_tokens` thinking; Opus 5 and Sonnet 5 reject sampling params and require adaptive thinking).
The capability table in `config/models.yaml` is load-bearing, not documentation.

---

## D-011 — Percentile bootstrap rather than BCa

**Status:** PROPOSED · **Date:** 2026-09-13 · **Phase:** P0 · **Affects:** P4

**Recommendation.** Paired **percentile** bootstrap, B=10,000, seeded.

**Alternatives.** _BCa_ corrects for bias and skew and is the more "correct" interval. _Normal
approximation_ is cheapest and wrong for a bounded, discrete, skewed metric at this sample size.

**Rationale.** At n≈150 with a win-or-tie rate away from the boundaries, the acceleration correction
moves the interval marginally. Percentile bootstrap is something I can explain line by line and
defend under questioning; BCa is something I would be reciting. For a project whose entire purpose is
defensibility, the simpler method I fully understand beats the sophisticated one I do not.

**Consequence.** If a route's rate lands very near 0 or 1, percentile intervals are known to be
anti-conservative. If P4 shows that happening, revisit and record a follow-up decision.

---

## D-012 — Benjamini–Hochberg FDR correction across route × rung tests

**Status:** PROPOSED · **Date:** 2026-09-13 · **Phase:** P0 · **Affects:** P4, P5

**Recommendation.** Apply BH-FDR at α across all `(route, rung)` significance tests within a single
policy fit.

**Alternatives.** _No correction_ — the common practice in eval tooling, and wrong: ~8 routes × 2
candidate rungs at α=0.05 yields roughly one spurious "significant" result per fit, meaning a route
gets demoted on noise about every other run. _Bonferroni_ controls family-wise error but is so
conservative at 16 tests that nothing would ever be demoted, making the router useless.

**Rationale.** BH controls the expected _proportion_ of false demotions, which matches what actually
matters here: a small number of wrong demotions among many correct ones is tolerable; a policy where
half the demotions are noise is not.

---

## D-013 — Gateway owns retry, backoff and the circuit breaker; SDK retries disabled

**Status:** PROPOSED · **Date:** 2026-09-13 · **Phase:** P0 · **Affects:** P1

**Recommendation.** Set `maxRetries: 0` on every provider SDK client. The gateway implements retry
with exponential backoff + full jitter, per-request deadlines, and a per-(provider, model) circuit
breaker.

**Alternatives.** _Use SDK defaults_ (the Anthropic SDK retries twice). Rejected: two retry policies
compose into an unpredictable one, wall-clock latency becomes `timeout × (retries+1)` in a way the
deadline logic cannot see, and — decisively — **the breaker never observes the failures it exists to
count**, because the SDK absorbs them.

**Rationale.** A circuit breaker is only meaningful if it sees every failure. Retry and breaker are
one policy and must live in one place.

**Consequence.** More code in the gateway, and a per-SDK unit conversion trap to watch: the
TypeScript SDK's `timeout` is in **milliseconds** while the Python SDK's is in seconds.

---

## D-014 — Rate limiting fails open when Redis is down

**Status:** PROPOSED · **Date:** 2026-09-13 · **Phase:** P0 · **Affects:** P1

**Recommendation.** If Redis is unreachable, allow the request, log at WARN, and increment a
degraded-mode counter.

**Alternatives.** _Fail closed_ (reject all traffic) is correct when the rate limiter is a billing or
abuse perimeter — a Redis outage then must not become free unlimited inference.

**Rationale.** Verdict is a single-tenant portfolio gateway; the rate limiter protects against
runaway loops, not against adversaries. Availability is worth more than quota enforcement here.

**This decision is explicitly context-dependent and I should say so in interviews**: for a
multi-tenant product with per-customer quotas, the correct answer flips to fail-closed, and the cost
cap (failure mode #12) is the control that _must_ fail closed regardless.

---

## D-015 — Embeddings as typed columns, not a polymorphic embeddings table

**Status:** PROPOSED · **Date:** 2026-09-13 · **Phase:** P0 · **Affects:** P5, P6

**Recommendation.** `vector(D)` columns directly on `corpus_items`, `routes` and
`semantic_cache_entries`.

**Alternatives.** _A shared `embeddings(owner_type, owner_id, model, vector)` table._ Avoids
duplicating the column and centralises the embedding model, but requires an untyped polymorphic
foreign key that the database cannot enforce, and forces a join on the request hot path.

**Rationale.** Three owners is not enough duplication to justify losing referential integrity and
adding a hot-path join. The embedding model is centralised in `config/models.yaml` instead, which is
where it belongs.

**Consequence.** Changing the embedding model or dimension is a migration touching three tables and
invalidating two HNSW indexes. That is genuinely expensive — hence `D` is treated as a
migration-breaking constant and gets its own decision entry when chosen in P2.

---

## D-016 — Schema applied by an explicit migrate service, not Postgres's initdb hook

**Status:** PROPOSED · **Date:** 2026-09-14 · **Phase:** P0 · **Affects:** every phase

**Decision.** `docker-compose.yml` runs a one-shot `migrate` service that applies
`db/migrations/*.sql` in filename order on every `up`. `gateway` and `evald` declare
`depends_on: migrate: condition: service_completed_successfully`, so neither starts before the
schema exists.

**Alternatives rejected.**

- _Mount `db/migrations` into `/docker-entrypoint-initdb.d`._ The obvious approach, and what P0
  originally did. It is silently broken: Postgres runs that hook **only when the data directory is
  empty**. This was not hypothetical — the first `docker compose up` on my machine bound a
  pre-existing `verdict_pgdata` volume left over from an earlier project, Postgres skipped the hook
  without a word, and the stack came up "healthy" with somebody else's schema and none of mine. A
  developer cloning the repo onto a machine with a stale volume would get a gateway that starts
  cleanly and fails on its first query.
- _A migration library (node-pg-migrate, Alembic)._ Correct at scale and gives rollback and
  ordering guarantees. Rejected for now: it puts the schema behind one service's runtime, and the
  schema is shared by a TypeScript service, a Python service and CI. Plain SQL applied by `psql` is
  the only form all three can run without importing each other's toolchain.

**Rationale.** "Works from a clean clone" has to mean "works on a machine that is not clean." A
mechanism that only fires under a precondition the developer cannot see is worse than no mechanism,
because it fails silently rather than loudly. Running idempotent migrations unconditionally on every
boot costs a second and removes the precondition entirely.

**Consequence.** Every migration must be idempotent (`IF NOT EXISTS`, `DO $$ ... EXCEPTION WHEN
duplicate_object`). CI enforces this by applying the full migration set twice and requiring both
runs to succeed. When migrations eventually need rollback or out-of-order application, that becomes
its own decision entry rather than a library adopted silently.

---

## D-017 — Cost is metered from provider-reported usage only, with an explicit finality flag

**Status:** ACCEPTED · **Date:** 2026-09-14 · **Phase:** P1 · **Affects:** P2–P6

**Decision.** The cost meter records only token counts a provider actually reported. It updates the
moment a usage-bearing event arrives and never estimates in between. Every trace carries
`usage_is_final`, saying whether the provider confirmed its totals.

**The constraint that forced the question.** "Cost computed incrementally during the stream" is
literally achievable for Anthropic — input tokens arrive at `message_start` and a cumulative output
count on every `message_delta` — but not for OpenAI or Groq, which report usage once in a final
chunk. There is no way to make those two incremental without inventing numbers.

**Alternatives rejected.**

- _Local tokenizer estimate, reconciled at the end._ Would give a genuinely incremental figure on
  every provider. Rejected because Anthropic's tokenizer is not public and `tiktoken` is wrong for
  Claude, so the live figure would be a guess — and a guess that briefly occupies a cost field is
  exactly what non-negotiable #1 forbids. The reconciliation would also hide how wrong the estimate
  had been.
- _Silently treat an unconfirmed count as final._ The simplest code and the most dangerous: a stream
  that aborts mid-token yields a token count that is a FLOOR, and averaging those into P5's Pareto
  curve would understate the cost of exactly the models that fail most often.

**Rationale.** "Not after the stream" is satisfied without estimating: there is no second pass and no
re-parsing of the response, so the number is final the instant the stream ends. Where a provider
cannot support that, the honest move is to say so in a column rather than to fabricate a number that
looks the same as a real one.

**Consequence.** P2 onwards must filter to `usage_is_final = true` for any cost claim, and say that
it does. The 0002 migration adds a partial index over unconfirmed rows so the exceptions are cheap
to find.

**Implementation note.** Arithmetic is integer nano-USD per token, not floating-point dollars. A
long stream updates the meter thousands of times; float accumulation would drift, and the drift
would land in the column that feeds every cost number in the project. The config schema enforces
that every price is an exact multiple of $0.001/MTok so the conversion is lossless.

---

## D-018 — Provider adapters use the official SDKs

**Status:** ACCEPTED · **Date:** 2026-09-14 · **Phase:** P1 · **Affects:** P1, P2

**Decision.** `@anthropic-ai/sdk` for Claude, and the `openai` package for both OpenAI and Groq
(Groq exposes an OpenAI-compatible endpoint, so one client class serves both via a `baseURL`
override). `maxRetries: 0` on every client.

**Alternatives rejected.**

- _Raw `fetch` plus a hand-written SSE parser._ Maximum control, no dependency drift, and permits
  byte-level passthrough for OpenAI and Groq. Rejected because we must translate Anthropic's
  protocol into OpenAI's anyway, so byte passthrough is impossible on the rung that matters most —
  and the remaining benefit is ~300 lines of `text/event-stream` framing whose bugs (multi-line
  `data:`, comment frames, an event split across TCP chunks) only appear under load.
- _Hybrid: SDK for Anthropic, raw for OpenAI/Groq._ Two error-mapping strategies and two code paths
  for the same outcome.

**Rationale.** The SDKs stream as async iterables of typed events, so "no buffering" is preserved.
The interview value of this project is the cost meter, the breaker, the abort handling and the
statistics — not a re-implementation of SSE framing.

**Consequence.** `maxRetries: 0` is load-bearing, not incidental. The Anthropic SDK retries twice by
default; leaving that on would mean two retry policies composing into an unpredictable one, and the
circuit breaker would never observe the failures it exists to count (D-013).

---

## D-019 — Retry only before the first byte reaches the client

**Status:** ACCEPTED · **Date:** 2026-09-14 · **Phase:** P1 · **Affects:** P1

**Decision.** The retry wraps opening the upstream stream _and pulling its first event_, not the
whole stream. Once any delta has been written to the client we are committed: a later failure
produces a truncated-but-well-formed stream, never a retry.

**Alternatives rejected.**

- _Retry the whole stream._ Impossible without either buffering the entire response before sending
  anything (which defeats streaming) or re-emitting text the client has already rendered.
- _Buffer until complete, then send._ Correct retries, but turns a streaming proxy into a
  non-streaming one and destroys the time-to-first-token that is the point of streaming.

**Rationale.** There is no way to un-say text a client has already displayed. Naming the commit
point explicitly is what makes the failure behaviour predictable: before it, transient errors are
invisible to the user; after it, they are a clean truncation with `finish_reason: "length"` and a
`[DONE]`, so the client's parser terminates instead of hanging.

---

## D-020 — Client disconnect is detected on the response stream, not the request stream

**Status:** ACCEPTED · **Date:** 2026-09-14 · **Phase:** P1 · **Affects:** P1

**Decision.** The disconnect listener is `reply.raw.on("close")`, guarded by
`if (reply.raw.writableEnded) return`. Not `req.raw.on("close")`.

**Why this is written down.** The obvious implementation is wrong in a way that passes review. For a
Node `IncomingMessage`, `close` fires when the **request body has been fully read** — which for a
normal POST is immediately, long before the client goes anywhere. Listening there made every
streaming request look like an instant disconnect: the gateway aborted its own upstream call and
returned an empty HTTP 200 with no error and nothing in the logs.

Every `inject()`-based test passed, because `inject()` never exercises a real socket. Only the
integration tests that listen on a real port and speak real HTTP caught it.

**Rationale.** A response socket closing while `writableEnded` is still false is the only signal that
actually means "the peer went away mid-response."

**Consequence.** Tests for anything socket-lifecycle-shaped must use a real listener. That is why
`apps/gateway/src/testing/mock-provider.ts` is an actual HTTP server rather than a stubbed adapter —
a stub cannot reproduce a socket destroyed mid-stream, which is the other case that matters here.

---

## D-021 — 1,200 gradable + 300 free-form, not an even split

**Status:** ACCEPTED · **Date:** 2026-09-14 · **Phase:** P2 · **Affects:** P3, P4, P5

**Decision.** ~1,500 items as briefed, but split 1,200 gradable (GSM8K, MMLU-Pro) to 300 free-form
(CNN/DailyMail summarisation, Dolly long-form QA, Bitext support replies) rather than 750/750.

**Rationale.** The gradable slice is scored by an exact deterministic verifier, so it needs **no LLM
judge at all**. The free-form slice needs a judge on every item, in both A/B positions, times
replicates. Free-form size therefore drives essentially the whole P3 bill; gradable size is nearly
free by comparison. Planning estimates at the time of the decision:

| Split       | Replay (7 rungs) | P3 judge | Total |
| ----------- | ---------------- | -------- | ----- |
| 750 / 750   | ~$23             | ~$216    | ~$240 |
| 1,200 / 300 | ~$19             | ~$43     | ~$62  |

Same corpus size; one fits the D-002 budget and the other is 3x over it. The measured projection for
the final corpus is **$28.91** for 13,300 calls including K=3 replicates.

**Alternatives rejected.** _750/750 as briefed_ — widest judge coverage, but unaffordable without
raising the budget or dropping to one replicate and a cheaper judge. _600/300_ — cheapest, but gives
up statistical power on the slice that costs almost nothing to scale.

**Consequence.** Routing conclusions on verifiable tasks will have much tighter confidence intervals
than on free-form tasks. That asymmetry must be stated in P5 rather than glossed over: the strong
claim is about extraction/classification-shaped work, and the free-form claim is weaker.

---

## D-022 — Difficulty is measured by a pilot, not assumed

**Status:** ACCEPTED · **Date:** 2026-09-14 · **Phase:** P2 · **Affects:** P3, P5

**Decision.** Use MMLU-**Pro** (10 options, built to resist ceiling effects) rather than MMLU, then
run a ~200-item pilot across three rungs and keep only items where the rungs actually disagree — at
least one pass and at least one fail. The pass rates before and after are written to
`artifacts/difficulty-pilot.json`.

**The problem this solves.** GSM8K and MMLU are among the most-quoted public benchmarks and are
near-certainly in training data. If every rung scores ~95%, then the judge has almost no losses to
be validated against — Cohen's κ would be computed on a degenerate distribution — and routing has
nothing to learn, because every rung looks identical. DESIGN.md §9.1 rejected public eval sets partly
for this reason; using them for _judge validation_ is defensible, but only if they discriminate.

**Alternatives rejected.** _Plain GSM8K + MMLU_ — simplest and most recognisable, but a ceiling
effect would not surface until P3 or P5, after the money was spent. _Harder variants only_ — still
assumes rather than measures. _Filter only, no harder source_ — would discard most of a contaminated
pool to find a thin discriminative band.

**Rationale.** It converts contamination from a threat we hope is absent into a number we report.
"I measured the pass-rate spread and filtered to the discriminative band" is a far stronger answer
than "I used GSM8K."

**Consequence.** The filtered corpus is no longer a uniform sample of GSM8K/MMLU-Pro, so it cannot be
compared to published accuracy figures on those benchmarks. That is an acceptable trade — Verdict
measures _relative_ rung quality, not absolute benchmark scores — but the README must say so.

---

## D-023 — Replicates: K=3 on a 200-item subset, K=1 elsewhere

**Status:** ACCEPTED · **Date:** 2026-09-14 · **Phase:** P2 · **Affects:** P4

**Decision.** Three samples per (item, model) for the first 200 slugs; one sample for the rest. The
measured within-model variance is then propagated when widening confidence intervals corpus-wide.

**Rationale.** D-009 established that `temperature` cannot be pinned on Claude Opus 5 or Sonnet 5
(HTTP 400), so sampling variance is real and must be measured rather than eliminated. Measuring it
everywhere would triple replay cost (~$57 instead of ~$19 on this corpus); measuring it on a subset
costs ~15% extra. "I measured the variance on a subset and propagated it" is what a statistician
would actually do.

**Consequence.** Determinism is **asymmetric across the ladder** — the OpenAI and Groq rungs are
pinned at `temperature: 0`, the Anthropic mid and strong rungs are not — and `request_params` encodes
exactly that from each model's capability table. Per-item error bars outside the subset are
propagated, not directly observed, and P4 must label them that way.

---

## D-024 — Replay runs through the gateway, not directly against providers

**Status:** ACCEPTED · **Date:** 2026-09-14 · **Phase:** P2 · **Affects:** P3, P5

**Decision.** The replay runner is an HTTP client of the P1 gateway. Batch API support is deferred
behind the same `complete()` interface.

**Rationale.** Every benchmark run then exercises the real adapters, cost meter, retry policy,
circuit breaker and parameter normalisation, and produces real trace rows. A second path straight to
the providers would be a second cost implementation that could silently disagree with the one
serving production traffic — and the entire project rests on those two numbers being the same.

**Alternatives rejected.** _Batch API from the start_ — halves cost immediately, but bypasses the
gateway entirely (no traces, no streaming, different endpoint) and needs job submission, polling and
result reconciliation before anything runs at all. _Direct provider SDK calls_ — fastest to write,
but forfeits the dogfooding and duplicates cost logic.

**Consequence.** `make bench` requires the stack to be up. The 50% Batch discount is left on the
table in P2 and should be taken in P3, where judging is the dominant cost.

---

## D-025 — Duplicate prompts are rejected at corpus assembly

**Status:** ACCEPTED · **Date:** 2026-09-14 · **Phase:** P2 · **Affects:** P4

**Decision.** `build_corpus` raises if two items share prompt text, and every source loader
deduplicates before sampling.

**Why this is written down.** It was found by a test, then confirmed on real data: the Bitext support
dataset contains repeated customer messages, and the first real corpus build failed on
`support_reply-0033` duplicating `support_reply-0014`.

The replay cache is content-addressed on (model, messages, params, replicate). Two items with
identical prompts therefore **share a single generation** while being counted as two independent
observations. That is not a cosmetic problem: it understates variance and narrows every confidence
interval built on them, which is precisely the error P4 exists to avoid making.

**Consequence.** Source loaders scan a wider window than they need (up to 60x the target for Bitext)
because deduplication removes a large fraction of candidates.
