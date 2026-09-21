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

---

## D-026 — The difficulty filter has two modes, because a cheap-only pilot can claim less

**Status:** ACCEPTED · **Date:** 2026-09-14 · **Phase:** P2 · **Amends:** D-022

**Decision.** `filter_by_difficulty` takes a `mode`:

- `discriminative` (default) — keep items with at least one pass **and** at least one fail. Correct
  only when the pilot spanned a real capability range (cheap, mid and strong).
- `drop_easy` — keep everything except items **every** pilot model got right. Correct when the pilot
  used cheap models only.

**Rationale.** The contamination risk D-022 addresses is one-sided, and the filter should be too.
An item every model answers correctly is provably uninformative no matter which models were in the
pilot — it cannot separate a cheap rung from an expensive one and gives the judge no loss to be
validated against. But an item every model gets **wrong** is only uninformative if the strongest rung
was present. Two small open models both failing says nothing about whether a frontier model would
succeed, and those are precisely the items where cheap-versus-strong routing is decided. Dropping
them on a cheap-only pilot would discard the most informative part of the corpus.

**What prompted it.** Running the pilot on Groq's free plan (`openai/gpt-oss-20b` and
`openai/gpt-oss-120b`, no payment) is a way to validate the entire live pipeline before spending
anything. That pilot is real and useful, but it cannot support the `discriminative` claim, and
silently applying the stricter filter to it would have quietly mis-filtered the corpus.

**Consequence.** The pilot report records `filter_mode` and `pilot_models`, so any later reader can
see exactly what the filtering is entitled to claim. A `drop_easy` filter removes the ceiling-effect
problem but leaves the too-hard tail in place; re-running in `discriminative` mode once a strong rung
is affordable is a strict improvement and is cheap, because the replay cache makes the already-piloted
calls free.

---

## D-027 — Rate limits do not open the circuit breaker

**Status:** ACCEPTED · **Date:** 2026-09-15 · **Phase:** P2 · **Amends:** D-013

**Decision.** `ProviderFailure` carries an explicit `countsTowardBreaker` flag. HTTP 429 is
`retryable: true, countsTowardBreaker: false`. 5xx, connection failures, timeouts and auth failures
count; 400 does not.

**What prompted it — the first real run.** The first pilot against a live provider (Groq's free plan,
30 requests/minute) produced:

| Outcome            | Count |
| ------------------ | ----- |
| 503 `breaker_open` | 339   |
| 200 success        | 86    |
| 429 `rate_limited` | 7     |

Seven rate limits crossed the breaker's threshold of five. The breaker opened for its 30-second
cooldown and refused the other 339 queued requests instantly. Over half the pilot was lost, and the
resulting pass rates were computed on 44 of 100 items without anything marking them as degraded.

**Rationale.** A circuit breaker exists to stop calling a provider that is **broken**. HTTP 429 means
the provider is healthy and asking the client to slow down. Conflating the two is a category error,
and the correct responses are opposite: one is _stop calling_, the other is _pace yourself_. Counting
back-pressure as ill health converts a throughput problem into an outage.

Auth failures do still count: a bad key fails every request until a human intervenes, so failing fast
is right. A 400 does not: it is our bug, and says nothing about provider health.

**Consequence.** A provider that throttles everything will now be retried with backoff indefinitely
rather than short-circuited. That is the correct behaviour, but it makes client-side pacing necessary
rather than optional — hence D-028.

---

## D-028 — The replay runner paces itself to a published rate limit

**Status:** ACCEPTED · **Date:** 2026-09-15 · **Phase:** P2 · **Affects:** P3

**Decision.** A thread-safe token bucket (`--rpm`) throttles outgoing calls to the provider's
published limit. `--rpm 0` disables it.

**Rationale.** Retrying a 429 is correct but wasteful: every one is a round trip that buys nothing
and, on a free tier where the limit is low, most of the run becomes retries. Knowing the published
limit and staying under it is strictly better. A token bucket rather than a fixed delay, because
bursts are fine as long as the average holds — which is exactly how published RPM limits are defined.

**Consequence.** `--rpm` must be set per provider, and it is a property of the account tier rather
than of the model, so it belongs on the command line rather than in `config/models.yaml`.

---

## D-029 — The difficulty pilot samples the gradable slice stratified, not head-first

**Status:** ACCEPTED · **Date:** 2026-09-15 · **Phase:** P2 · **Amends:** D-022

**Decision.** `_pilot_sample` takes a seeded, balanced sample across every gradable task type.

**What prompted it.** The first pilot took the first N verifiable items. Slugs sort alphabetically,
`math_word_problem` precedes `multiple_choice`, and the corpus holds 600 of each — so a 100-item
pilot tested **only GSM8K**. MMLU-Pro, the half chosen specifically because it resists ceiling
effects (D-022), was never measured, and its pass rates would have been silently attributed to the
whole gradable slice.

**Rationale.** A pilot that measures one task type and reports a number for two is not a measurement
error, it is a mislabelled result — the kind that survives into a README and then into an interview.

**Consequence.** Per-task pass rates should be reported separately in the pilot artifact, since the
two sources were chosen for different reasons and there is no reason to expect the same ceiling
behaviour from both.

---

## D-030 — GSM8K replaced with hard MATH, on measured evidence

**Status:** ACCEPTED · **Date:** 2026-09-15 · **Phase:** P2 · **Supersedes part of D-022**

**Decision.** The maths half of the gradable slice is `nlile/hendrycks-MATH-benchmark`, restricted to
difficulty **levels 3–5** and to problems whose answer is a **plain number** (so the existing
`final_number` verifier works unchanged). GSM8K is removed from the corpus entirely.

**The measurement that forced it.** The first clean pilot, on two free Groq models:

| Source   | gpt-oss-20b | gpt-oss-120b | Discriminative items |
| -------- | ----------- | ------------ | -------------------- |
| GSM8K    | 90%         | **98%**      | **5 / 50**           |
| MMLU-Pro | 38%         | 54%          | 32 / 50              |

A 120B open model scoring 98% means every paid rung scores 98–100%. Those 600 items could not
separate a cheap rung from an expensive one, gave the judge almost no losses to be validated
against, and would still have cost money to replay across seven models.

Critically, the **aggregate** pass rate was 0.76 / 0.64 — healthy-looking, and completely hiding the
split. Only the per-task breakdown (D-029) revealed it.

**After the swap, same pilot protocol:**

| Source          | gpt-oss-20b | gpt-oss-120b | Discriminative items |
| --------------- | ----------- | ------------ | -------------------- |
| MATH levels 3–5 | 28%         | 30%          | **40 / 50**          |
| MMLU-Pro        | 38%         | 54%          | 32 / 50              |

**Alternatives rejected.** _More MMLU-Pro_ — proven to work and free to adopt, but collapses the
gradable slice to a single task shape, and multiple-choice is not representative of real LLM
traffic. _AIME_ — integer answers and no ceiling risk, but likely too hard in the other direction.
_Drop maths entirely_ — gives up power on the slice that costs almost nothing to scale.

**Consequence, and the remaining open question.** The numeric-answer filter is strict: only ~~600
usable items exist across both MATH splits, so the maths slice is now exactly 600 with no spare
capacity. More importantly, **31 of 50 piloted maths items were failed by BOTH cheap models.** That
is the right shape for a cheap-versus-strong routing decision, but it means the ceiling risk has been
traded for a floor risk: if the strong rungs also fail them, the slice discriminates nothing again.
Resolving that needs a strong model in the pilot, which needs real spend (~~$1). Until that runs, the
maths slice is **verified not-too-easy but not yet verified not-too-hard**, and no claim should be
made about it.

---

## D-031 — Judge design: Opus 5, shared rubric skeleton, prompt-validated JSON

**Status:** ACCEPTED · **Date:** 2026-09-15 · **Phase:** P3 · **Implements:** D-003

**Decision.** The judge is `claude-opus-5`. The rubric is one shared skeleton (verdict scale, tie
rules, output contract) plus a per-task criteria block, under a single `RUBRIC_VERSION`. Verdicts are
prompt-driven JSON validated strictly in code, with one retry, then `unparseable`.

**Why Opus rather than something cheaper.** The reference outputs are Opus, so every comparison is
"candidate vs Opus" — which makes the _direction_ of self-preference matter more than its size. An
Opus judge biases candidates to look **worse**, i.e. toward keeping the expensive model. That is the
conservative direction, and the same direction D-004's `ci.low >= F` rule deliberately errs in. A
GPT-family judge would have been cheaper and family-neutral to the reference, but it shares a family
with two _candidate_ rungs, so its bias would push toward **wrongly demoting** a route. Safe-direction
bias beats no-direction bias.

The other reason is economic in the currency that is actually scarce: a judge that fails the
κ ≥ 0.6 gate costs a full re-label — hours of Iraa's time — not dollars.

**Why not constrained decoding.** Structured output is not available uniformly across Anthropic,
OpenAI and Groq, and the judge must behave identically across judge models or the calibration does
not transfer. Prompt-driven JSON with strict validation is uniform, and DESIGN.md failure mode #11
already specified the retry-then-exclude behaviour.

**Why one rubric rather than three.** Three rubrics are three instruments and honestly need three
calibrations — roughly three times the labelling. The shared skeleton keeps it one instrument with
one set of labels, while per-task κ is still reported, which is where a weakness would surface.

**Consequence.** `RUBRIC_VERSION` is load-bearing: any edit to the rubric text invalidates every
label calibrated against it. The judge's token budget is capped at 700, because on adaptive-thinking
models an uncapped budget turns a cheap classification into an expensive essay.

---

## D-032 — The κ gate is on the confidence interval's lower bound

**Status:** ACCEPTED · **Date:** 2026-09-15 · **Phase:** P3 · **Affects:** P4, P5

**Decision.** The judge passes only when the **lower bound** of the bootstrap CI on Cohen's κ is
≥ 0.6, not when the point estimate is.

**Rationale.** A κ of 0.62 with an interval from 0.45 to 0.78 has not demonstrated 0.6 — it is
consistent with a judge substantially worse than the gate. This is the same rule as D-004's routing
decision, applied to the instrument rather than to the models, and for the same reason: small samples
should produce refusals, not optimistic verdicts.

**Why κ rather than raw agreement.** Raw agreement is inflated by chance and by class imbalance. A
judge that answers "tie" to everything scores 33% raw agreement on a balanced sample and is worthless;
κ scores it 0. There is a test asserting exactly that.

**Consequence.** With 200 labels the CI half-width is roughly ±0.10, so a point estimate below about
0.70 will not clear the gate. If that happens the honest response is in the report: the diagnosis
names the specific failure mode (tie-happy, over-deciding, position-dependent, directionally biased,
concentrated in one task), the rubric is revised, `RUBRIC_VERSION` is bumped, and a **fresh** sample
is labelled. Reusing the same labels against a revised rubric would overfit the rubric to them, and
the resulting κ would be meaningless.

---

## D-033 — Human labels are blind, resumable, and record their own provenance

**Status:** ACCEPTED · **Date:** 2026-09-15 · **Phase:** P3

**Decision.** The labelling harness never shows model identity or the judge's verdict. Pairs are
sampled stratified across (task type × candidate model) with the candidate's slot randomised per
pair. Every label is appended to disk immediately and records the slot shown and the time taken.

**Rationale.** Each property defends a specific way the κ could be meaningless:

- **Blind to model** — otherwise the labels measure expectation ("the expensive one is probably
  better") rather than quality.
- **Blind to the judge** — otherwise the labels anchor on the judge and κ measures agreement with
  itself.
- **Stratified** — a κ computed on a sample dominated by one task or one model is a number about
  that corner, not about the judge.
- **Not sampled by judge uncertainty** — sampling the judge's hard cases would make κ a measure of
  its worst performance and incomparable to anything.
- **Slot randomised and recorded** — makes the _labeller's own_ position bias measurable instead of
  baked in.
- **Timing recorded** — a label made in two seconds is worth re-checking before blaming the judge
  for disagreeing with it. The diagnosis flags this explicitly.
- **Saved after every decision** — 200 labels is hours of work; losing it to a closed terminal is
  not an acceptable failure mode.

**Consequence.** `calibration/labels.jsonl` is committed. It is real measurement data and the human
half of every κ this project reports.

---

## D-034 — Why paired bootstrap rather than a t-test (the interview answer)

**Status:** ACCEPTED · **Date:** 2026-09-15 · **Phase:** P4 · **Implements:** D-011

**The question you will be asked:** "Why not just run a t-test?"

**The short answer, in one breath.** A t-test assumes the thing being averaged is roughly
bell-shaped and unbounded. What we are averaging is a difference of two rates between 0 and 1, built
from a three-valued outcome, on a few hundred items. None of those assumptions hold, and the
bootstrap does not need them — it works out the uncertainty by re-running the experiment on the data
we already have.

**The longer answer, in four parts.**

**1. The data is not shaped like a t-test expects.** Each item produces `win`, `tie` or `loss` — three
discrete buckets, not a continuous measurement. We turn that into a rate, and a rate is bounded at 0
and 1. When a model's win-or-tie rate is near 0.9, the distribution of the estimate is squashed
against the ceiling and visibly lopsided. A t-test would draw a symmetric interval that extends past
1.0, which is not a possible value. The bootstrap interval is built from actual resampled values, so
it cannot leave the range the data lives in.

**2. It is a difference of two dependent rates, not one mean.** Even a paired t-test assumes the
per-item differences are normally distributed. Here each per-item difference can only be one of a
handful of values (−1, 0, +1 for the binarised metric). A t-test on that is an approximation whose
error depends on the sample size and the rate, and we have no way to check it. The bootstrap makes
no claim about the shape at all.

**3. Pairing is where most of the variance goes, and it must be preserved.** The same items are
scored by both arms. Some items are simply hard and drag both arms down together; some are easy and
lift both. That shared item difficulty is the single largest source of variance, and it cancels
exactly when the arms are compared item by item. We resample **items**, carrying each item's pair of
outcomes together, so the cancellation inside every resample matches the cancellation in the real
data. `test_identical_arms_give_a_zero_effect_and_a_zero_width_interval` is the proof this is wired
up: two identical arms produce an interval of exactly zero width, because every resample cancels.
An unpaired bootstrap would report spurious width there, and a two-sample t-test would too.

**4. The honest reason, which is the one worth saying out loud.** With a t-test I would be asserting
that the sampling distribution is approximately normal, and I could not check it. With the
bootstrap I make no such assertion and can explain every line of the implementation. For a project
whose entire claim is "this number is trustworthy", a method I can fully defend beats a method that
is marginally more powerful under assumptions I cannot verify.

**What a t-test would actually get wrong here:** intervals that extend outside [0, 1] at high rates;
overconfident intervals when the discordant count is small, which is the common case with two similar
models; and no way to notice either problem had happened.

**When a t-test would have been fine.** If the metric were a continuous score (a 1–5 rubric averaged
over many items) with n in the thousands, the Central Limit Theorem would do the work and the two
methods would agree to three decimal places. The bootstrap costs a few seconds of CPU, so there is
no reason to take the risk of finding out we were in the other regime.

**Alternatives rejected.** _Paired t-test_ — as above. _BCa bootstrap_ — corrects for skew and bias
and is the more "correct" interval, but at n≈150 the acceleration term moves the interval marginally
and it is something I would be reciting rather than explaining (D-011). _Wilcoxon signed-rank_ —
distribution-free and a reasonable choice, but it tests a shift in the median of the differences and
reports no effect size in the units anyone cares about; the routing rule needs an interval in
win-or-tie rate points, not a rank statistic.

---

## D-035 — Four verdicts, because "no significant difference" hides two opposite findings

**Status:** ACCEPTED · **Date:** 2026-09-15 · **Phase:** P4 · **Affects:** P5

**Decision.** `REGRESSION`, `IMPROVEMENT`, `EQUIVALENT`, `INCONCLUSIVE` — decided by where the
confidence interval sits relative to a practical-significance margin (default ±0.03 in win-or-tie
rate points), not by whether a p-value crosses 0.05.

| Interval vs margin      | Verdict        | Means                                                           |
| ----------------------- | -------------- | --------------------------------------------------------------- |
| entirely below −margin  | `REGRESSION`   | worse, by an amount that matters                                |
| entirely above +margin  | `IMPROVEMENT`  | better, by an amount that matters                               |
| entirely inside ±margin | `EQUIVALENT`   | **evidence of absence** — we measured precisely and it is small |
| straddles a boundary    | `INCONCLUSIVE` | **absence of evidence** — this run cannot tell                  |

**Rationale.** "No significant difference" is the weakest claim in statistics and it conflates two
opposite situations: a precise measurement of a small effect, and no useful measurement at all. A run
on 20 items with an enormous interval and a run on 20,000 items with a tight one would both report
it. The first must not license demoting a route; the second should.

This matters here specifically because D-004's routing rule is already a non-inferiority test
(`ci.low >= floor`). P4 should speak the same language, or the two layers would be making
incompatible kinds of claim about the same data.

The margin also separates statistical from practical significance. A 1.5-point difference measured
over 20,000 items is real and detectable and nobody cares; `test_separates_statistical_from_practical_significance`
pins that case as `EQUIVALENT` while still reporting `statistically_significant: true`.

**Consequence.** The margin is a product judgement, not a statistical one, so it lives in config and
must be chosen before looking at results. `can_demonstrate_equivalence` reports whether the interval
is even narrow enough to fit inside the margin — a run wider than that can still reach `REGRESSION`
or `IMPROVEMENT` if the effect is large, but can never reach `EQUIVALENT`, and says so.

---

## D-036 — McNemar on win-or-tie, exact below 25 discordant pairs

**Status:** ACCEPTED · **Date:** 2026-09-15 · **Phase:** P4

**Decision.** Success = win-or-tie against the frozen reference, matching D-003's metric and D-004's
floor exactly. The exact binomial test is used when there are fewer than 25 discordant pairs, the
chi-square approximation with Yates' correction above that.

**Why McNemar and not a two-proportion test.** With two arms scored on the same items, every item
lands in one of four cells. The two concordant cells carry no information about which arm is better —
an item both arms get right tells you the item was easy, not that the arms are equal. All the
evidence is in the discordant cells, and McNemar conditions on exactly those. A two-proportion test
would treat the concordant items as evidence and dilute a real effect with items that could never
have shown it. `test_concordant_pairs_carry_no_evidence` asserts this directly: padding the sample
with 500 items both arms get right leaves the p-value unchanged.

**Why exact rather than chi-square by default.** With two similar models on a few hundred items,
most items are concordant and the discordant count is routinely under 25 — which is precisely where
the chi-square approximation is unreliable. Getting it wrong is worst exactly where it matters most.

**Why implemented rather than imported.** scipy is the reason this service is in Python at all, but
testing a scipy call against scipy proves nothing. The test suite checks our exact test against
`scipy.stats.binomtest` across a 16×16 sweep of discordant splits, our chi-square against
`scipy.stats.chi2.sf`, and our Benjamini-Hochberg against `scipy.stats.false_discovery_control`.
scipy is a dev dependency only.

**Consequence.** Counting a tie as success is the generous reading, biasing toward finding a cheap
model acceptable. That is deliberate: D-004's `ci.low >= floor` rule pulls the other way, so the two
controls are set against each other rather than compounding.

---

## D-037 — Hybrid confidence signal: self-consistency where exact, verifier elsewhere

**Status:** ACCEPTED · **Date:** 2026-09-15 · **Phase:** P5

**Decision.** Gradable items (maths, multiple choice) escalate on **self-consistency**: sample the
cheap model K=3 times and check whether the _extracted answers_ agree, reusing the verifiers built
and tested in P2. Free-form items escalate on a **cheap verifier model's** adequacy score.

**Rationale.** Self-consistency is only meaningful when "do these answers agree?" has an exact
answer. For a maths problem it does — two extracted numbers are equal or they are not, no second
model and no semantic guesswork. For two paragraphs of prose it does not: agreement is a question
about meaning, and the embedding model that would answer it cheaply is still unchosen
(`config/models.yaml` has it as `null`). A lexical similarity heuristic was rejected because it is a
poor proxy for semantic agreement, and the free-form half is exactly where routing decisions are
hardest — an unreliable signal there is worse than an honest extra call.

**Consequence.** The confidence check is **not free, and the cost model says so**. Gradable items pay
for K cheap calls; free-form items pay for one cheap call plus one verifier call; escalated items pay
for all of that _and_ the strong call. This makes always-escalating strictly **more expensive** than
never cascading at all, which the sweep shows directly (cost ratio 102.5% at τ=1.0 on synthetic
data). A cascade that omitted the confidence check from its cost model would report savings it does
not deliver, and that omission is the most common way cascade results are overstated.

---

## D-038 — Fit on dev, report on test, and assert the separation

**Status:** ACCEPTED · **Date:** 2026-09-15 · **Phase:** P5

**Decision.** The escalation threshold is fitted on the **dev** split and the chosen threshold is
evaluated once on the **test** split. `assert_disjoint` raises if a single slug appears in both. Only
the held-out numbers may be quoted, and the artifact records which split played which role.

**Rationale.** A threshold fitted on the data it is reported on will look excellent and mean nothing,
and — this is the dangerous part — the output gives no hint that it happened. A leaked fit is
indistinguishable from a good one by inspection. So the separation is checked in code rather than
trusted to discipline, and there is a test that plants a leak and asserts it is caught.

`calibration` is deliberately not used here: it is spoken for by P3's judge labels, and mixing the
instrument's training data into the policy's would couple two things that must be able to fail
independently.

**Consequence.** The report-split sweep is still written to `pareto.json`, clearly marked
`role: "report"`, purely so a reader can see the whole curve. It played no part in selection, and the
artifact says so in its notes.

**When nothing qualifies.** If no threshold on dev is shown non-inferior to always using the strong
model, `operating_point()` returns `None` and the artifact explains why. That is a real result — keep
using the strong model — not a failure of the sweep, and the message says so explicitly so it is not
quietly treated as a bug.

---

## D-039 — Route keys are task types, not embedding clusters (for now)

**Status:** ACCEPTED · **Date:** 2026-09-15 · **Phase:** P5 · **Amends:** D-004

**Decision.** The offline policy's route key is the corpus **task type**. At request time the gateway
takes the route from an explicit `x-verdict-route` header; with no hint, it serves `safe_default`.

**Rationale.** D-004 envisaged clustering prompt embeddings, but the embedding model is still
unchosen and its dimension is a migration-breaking constant (D-015). Picking one now to unblock P5
would bake an arbitrary choice into the policy and into two HNSW indexes. Task type is a real,
explainable routing key that needs no embeddings at all, and swapping in embedding clusters later
changes how `route_key` is _derived_ and nothing else — the policy schema, the gateway lookup and the
fitting code are all unaffected.

**Consequence, stated plainly.** The gateway cannot currently infer a route from the prompt, so an
un-hinted request gets no discount. That is the correct failure direction (DESIGN.md §8.2: ambiguity
resolves toward quality) but it does mean the live cost saving is only available to callers who label
their traffic. Prompt-side route inference is P6 work, arriving with the embedding model.

---

## D-040 — Cascade runs offline only; the live path serves the offline policy

**Status:** ACCEPTED · **Date:** 2026-09-15 · **Phase:** P5 · **Affects:** P7, P8

**Decision.** The gateway implements the offline per-route policy behind `ROUTER_MODE`, with a
per-request `x-verdict-router` override. The cascade is fitted and evaluated in `evald` but is **not**
in the live request path. `policy.json` carries the cascade configuration with `enabled: false`.

**Two reasons, both structural.**

**Streaming.** The confidence check needs the cheap model's _whole_ answer before it can decide. A
streaming request would therefore have to buffer the entire cheap generation, check confidence, and
only then start emitting — destroying the time-to-first-token that streaming exists for, and
destroying it _again_ on escalation. This is the same constraint as D-019's "retry only before the
first byte", one step worse.

**Two implementations of the verifiers.** The self-consistency signal compares _extracted_ answers,
using the regex verifiers written and tested in Python (P2). Putting the cascade in the gateway means
reimplementing them in TypeScript, and two implementations of the same extraction logic will
eventually disagree — which is precisely the failure D-024 avoided by routing replay through the
gateway instead of building a second cost path.

**Consequence.** The measured cascade result is an _offline_ result and the README must say so. The
live saving comes from the offline policy. Moving the cascade into the live path needs either a
shared verifier implementation (a WASM build, or an evald sidecar call) or acceptance of the
buffering cost, and that is a decision to make with measurements in hand rather than now.

---

## D-041 — The maths slice is confirmed usable; the ladder's middle is not yet separable

**Status:** MEASURED · **Date:** 2026-09-21 · **Phase:** P2/P5 · **Resolves:** D-030's open question

**The measurement.** First real multi-tier pilot: 60 gradable items (30 hard MATH, 30 MMLU-Pro)
across three rungs, 175 successful calls, **$0.4925 actual spend**.

| Model                | Overall | MATH 3–5 | MMLU-Pro |
| -------------------- | ------- | -------- | -------- |
| `openai/gpt-oss-20b` | 0.655   | 0.679    | 0.630    |
| `claude-haiku-4-5`   | 0.817   | 0.833    | 0.800    |
| `claude-sonnet-5`    | 0.817   | 0.800    | 0.833    |

**D-030 is resolved: the maths slice is NOT too hard.** Sonnet 5 scores 0.80 on MATH levels 3–5.
The ceiling risk was removed by D-030's swap away from GSM8K, and the floor risk it created has now
been ruled out by measurement rather than assumed away. The gradable slice discriminates.

**The finding that matters more.** Haiku 4.5 and Sonnet 5 scored **identically** — 0.8167 against
0.8167, effect exactly 0.0000, McNemar p = 1.000. Sonnet is a 2× more expensive rung. The tempting
reading is "no difference, route everything to Haiku, halve the bill."

P4 refuses that reading. Run through the verdict layer:

```
VERDICT: INCONCLUSIVE
  claude-haiku-4-5 -> claude-sonnet-5
  effect        +0.0000  [-0.1000, +0.1000]  (95% CI)
  n             60 items, 8 discordant
  resolution    0.1000   (too wide to ever show EQUIVALENT at this margin)
```

The interval is ±0.10 against a ±0.03 margin. A perfect zero and a p-value of exactly 1.0, and the
system still says _we cannot tell_. This is precisely the failure D-035 was built to prevent,
demonstrated on real data rather than argued in the abstract, and it is the clearest justification
in the project for having four verdicts instead of three.

By contrast, the wider gap IS conclusive at the same n:

```
VERDICT: IMPROVEMENT
  openai/gpt-oss-20b -> claude-haiku-4-5
  effect        +0.1636  [+0.0364, +0.2909]  (95% CI)
  McNemar p     0.022461  (exact, 13 discordant)
```

**Consequence.** To separate Haiku from Sonnet at a ±0.03 margin needs roughly **667 paired items**
(the half-width scales as 1/√n: 60 × (0.10/0.03)² ≈ 667). Until that runs, no claim may be made
about those two rungs in either direction. The Groq-to-Haiku gap, however, is already established.

**Caveat on the metric.** These verdicts use **verifier correctness** (exact match against ground
truth), not D-003's judge win-or-tie. It needs no judge, no labels and no further spend, so it is
the first statistically honest comparison available — but it is a different measurement and the
`evald verdict` output says so on every run.

---

## D-042 — Cost projections are now calibrated against measurement

**Status:** ACCEPTED · **Date:** 2026-09-21 · **Phase:** P2

**The gap.** The pilot projected **$0.2366** and cost **$0.4925** — a 2.1× under-projection. Cause:
per-task output assumptions of 300 tokens (maths) and 150 (multiple choice) against measured
averages of 440–953. Reasoning models spend heavily on chains of thought even to answer a
multiple-choice question.

**Decision.** The gradable task types now use measured medians (650 tokens), the free-form ones are
still assumptions, and `MEASURED_TASKS` records which is which. `TOKEN_ESTIMATE_METHOD`, written
into every artifact, now names both.

**Rationale.** A projection that is quietly wrong by 2× is worse than no projection: it is what a
user decides to spend money on. Making the estimate self-correcting — `compare_projection` already
records projected-versus-actual on every run artifact — is the point of having recorded both.

---

## D-043 — The runner reports progress

**Status:** ACCEPTED · **Date:** 2026-09-21 · **Phase:** P2

**What happened.** The pilot ran for ten minutes printing nothing. It was working — 24 calls a
minute, all succeeding — but from the terminal it was indistinguishable from a hang, and the correct
user response to an apparent hang is Ctrl-C, which would have discarded the run.

**Decision.** `run_replay` takes an `on_progress` callback, reporting completed/total, spend so far,
cache hits, errors and an ETA every five tasks.

**Rationale.** Silence is not neutral. A long-running command that gives no signal trains the user
to kill it, and a spend cap is no protection against a user who aborts a run they have already paid
for. Spend is shown live for the same reason: the number that matters most should never require a
database query to see.

---

## D-044 — Embedding model: `text-embedding-3-small`, D = 1536

**Status:** ACCEPTED · **Date:** 2026-09-21 · **Phase:** P6 · **Resolves:** D-015

**Decision.** `openai/text-embedding-3-small`, 1536 dimensions, pinned by
`db/migrations/0003_vectors.sql` and recorded in `config/models.yaml` with its verified price.

**Rationale.** Embeddings are effectively free: **$0.02 per million tokens**, so embedding the whole
1,500-item corpus costs about **$0.008**. That removes cost as a deciding factor and leaves
architecture, which is decisive — the TypeScript gateway can call this API directly, so the semantic
cache works on the live hot path with no dependency on `evald` and no new service in the request
chain.

**Alternatives rejected.** _`text-embedding-3-large`_ — better retrieval, still trivially cheap
(~$0.05 for the corpus), but 2× the index and 2× the vector storage for quality that near-duplicate
detection does not need. _A local model (bge-small via fastembed)_ — free, deterministic, runs in CI
with no key, and genuinely attractive; rejected because the gateway cannot run it, so the live cache
would need a round-trip to `evald` on every request or would become offline-only like the cascade
(D-040) and therefore save nothing in production.

**Consequence.** D-015's migration-breaking constant is now fixed at 1536 across
`corpus_items.embedding`, `routes.centroid` and `semantic_cache_entries.embedding`, with HNSW
indexes on the latter two. Changing it later means rewriting three columns and rebuilding two
indexes. It also unblocks D-039: route inference from prompt embeddings is now possible, so the
gateway will no longer need an explicit `x-verdict-route` hint to give a request its discount.

---

## D-045 — Calibration pairs are auto-derived, with HARD negatives

**Status:** ACCEPTED · **Date:** 2026-09-21 · **Phase:** P6

**Decision.** Positives are LLM paraphrases of real corpus items (same question, different words).
Negatives are, for each item, its **most similar different corpus item**. A human spot-checks a
random sample rather than labelling the whole set.

**Why hard negatives, specifically.** This is the part that decides whether the calibration means
anything. Two prompts drawn at random from a 1,500-item corpus are almost never similar, so _any_
threshold separates them and the resulting false-hit rate looks superb while saying nothing about
production — where the dangerous cases are prompts that are close but not the same. Nearest-neighbour
negatives are precisely the pairs sitting near the decision boundary, and they are the only ones
carrying information about where to put it.

**Why auto-derived.** Corpus items are distinct prompts by construction — assembly rejects duplicates
(D-025) — so "these two are different questions" needs no human to establish. Iraa's labelling time
is the scarcest resource in the project and P3 already claims 4–6 hours of it; spending more of it
confirming that two unrelated questions are unrelated would be waste. The spot-check exists because
the _positives_ are model-generated and could drift in meaning, which nothing else would reveal.

---

## D-046 — Threshold chosen on the false-hit UPPER bound, and it must also be useful

**Status:** ACCEPTED · **Date:** 2026-09-21 · **Phase:** P6

**Decision.** Pick the threshold with the best hit rate among those that are **provably safe** (the
false-hit rate's confidence upper bound is within tolerance, default 1%) **and useful** (hit rate at
or above 5%). If none qualifies, choose nothing and run no semantic cache.

**Rationale.** Judging safety on the upper bound rather than the point estimate mirrors D-004's
routing rule, which demotes a route only on a lower bound. Both err the same way: a small or noisy
sample refuses to loosen the cache rather than loosening it on luck. A false hit returns a
confidently wrong answer to a question nobody asked, so uncertainty has to cost hits, never safety.

**The usefulness floor was added because a test caught the gap.** With safety as the only criterion,
the selector happily chose a threshold so strict that nothing ever hit it — technically safe, and
worthless. A cache that rarely hits still costs an embedding call and a vector search on every
request, so it is strictly worse than no cache. "Safe" is necessary and not sufficient.

**Alternatives rejected.** _Maximise `hit_rate − k × false_hit_rate`_ — one clean optimum, but it
buries the safety tradeoff in a penalty weight nobody can justify, and will accept a worse false-hit
rate whenever the hit rate rises enough to pay for it. _Fix a conservative constant like 0.97_ —
easy to defend against overfitting, but leaves real hits on the table for no measured reason, and "I
picked 0.97 because it felt safe" is a weaker answer than "I picked the loosest threshold whose
false-hit rate I could bound under 1%".

---

## D-047 — Clopper-Pearson for the false-hit bound, because the bootstrap is wrong at zero

**Status:** ACCEPTED · **Date:** 2026-09-21 · **Phase:** P6 · **Amends:** D-011

**The bug, found in this project's own code.** D-011 established the percentile bootstrap for every
rate in the project. That is right for a mid-range proportion and **wrong for a rare event**:
resample 0 successes out of 150 any number of times and every resample still contains 0, so the
interval is `[0, 0]`. The first calibration run duly reported a false-hit rate of "0.00% with an
upper bound of 0.00%" for a threshold that had merely not been tested hard enough. Since D-046
chooses the threshold _on that upper bound_, the cache would have been loosened on the strength of
an artefact of the method.

**Decision.** The false-hit rate specifically uses a Clopper-Pearson interval, implemented by
inverting the binomial test and verified against `scipy.stats.beta.ppf` across the full range
including both boundaries. 0 of 150 becomes **2.43%**, not 0%. The hit rate keeps the bootstrap,
which is appropriate for a mid-range proportion and consistent with the rest of the project.

**The consequence is a result in itself.** A safety bound has a floor set by sample size rather than
by observations: proving a false-hit rate under 1% needs **at least 368 hard negatives even with zero
observed false hits** (the rule of three, ~3/n). Below that, "0%" means "untested", not "safe", and
the calibration says so explicitly instead of choosing a threshold.

---

## D-048 — Prompt version is part of the cache KEY, not a field on the row

**Status:** ACCEPTED · **Date:** 2026-09-21 · **Phase:** P6

**Decision.** Both caches key on `(canonicalised messages, model, prompt_version, params)`. Bumping
the prompt version makes every prior entry unreachable. `invalidatePromptVersion` additionally
deletes them.

**Rationale.** A cached answer is valid only for the prompt that produced it, and **nothing about a
stored response reveals that the prompt behind it has changed** — the text still looks like a
perfectly good answer. Storing the version as a field to be checked would make correctness depend on
every read path remembering to check it; putting it in the key makes stale entries unreachable by
construction, which fails closed. The model is in the key for the same reason: two models answering
the same question give different answers, and serving one for the other is a false hit dressed up as
a cache design.

**Two related details, both tested.** Only _user_ and _assistant_ turns are embedded: a shared system
prompt is identical across every request in a route, so including it drags every similarity toward
1.0 and destroys the discrimination the threshold depends on — and the system prompt is already
accounted for by `prompt_version`. And TTL is stored as an absolute `expires_at` filtered in SQL,
not as a duration, so a lapsed entry can never be served even if the sweeper has not run.

---

## D-049 — Embedding model switched to a local one, because D-044 needed an account we do not have

**Status:** ACCEPTED · **Date:** 2026-09-22 · **Phase:** P6 · **Amends:** D-044

**What went wrong.** D-044 chose `openai/text-embedding-3-small` partly on the argument that the
TypeScript gateway could call it directly. That argument was sound and the premise was not checked:
there is no OpenAI key on this account, and there never was. The decision was approved and the
calibration could not run.

**Decision.** `BAAI/bge-small-en-v1.5`, run locally in-process via `fastembed`. 384 dimensions, no
API key, no network after the first model download, free.

**Measured before committing to it**, on two real corpus prompts:

| Pair                              | Cosine     |
| --------------------------------- | ---------- |
| A question vs a paraphrase of it  | **0.9106** |
| Two genuinely different questions | **0.6701** |

Clean separation, which is the only property the threshold calibration needs.

**Why the amendment was free, and why that window mattered.** D-015 made the embedding dimension
migration-breaking: changing it means rewriting three columns and rebuilding two HNSW indexes. It
was free here only because both vector tables were still empty. Had a single calibration run
happened first, this would have been a data migration instead of a one-line config change. The
window is now closed.

**Consequence, stated plainly.** The gateway cannot run an ONNX model, so the live semantic cache
would need a round-trip to `evald` and is offline-only for now — the same position as the cascade in
D-040, and consistent with it. The calibration, which is what produces the reportable number, is
unaffected and now costs **$0.00**: embeddings are local, and the paraphrases run on Groq's free
tier.

**The lesson worth stating.** A decision that rests on an unverified premise about the environment
is a guess wearing a rationale. The premise ("we can call this API") was the cheapest thing in the
whole decision to check, and checking it was skipped because the reasoning around it felt solid.

---

## D-050 — MEASURED: the semantic cache is unsafe on this corpus, and is not deployed

**Status:** MEASURED · **Date:** 2026-09-22 · **Phase:** P6

**The result.** Calibrated against 200 paraphrase positives and 600 hard negatives, embeddings from
`BAAI/bge-small-en-v1.5`. **No threshold is both safe and useful** at a 1% false-hit tolerance, so
the gateway serves no semantic cache.

| Threshold | Hit rate | False-hit rate | CP upper bound | Acceptable    |
| --------- | -------- | -------------- | -------------- | ------------- |
| 0.850     | 51.0%    | 14.17%         | 17.22%         | no            |
| 0.900     | 32.0%    | 3.33%          | 5.10%          | no            |
| 0.950     | 12.0%    | 1.33%          | 2.61%          | no            |
| 0.975     | 1.0%     | 0.33%          | 1.20%          | no            |
| 1.000     | 0.0%     | 0.00%          | 0.61%          | safe, useless |

**The tradeoff, priced rather than argued.** "No safe threshold" is true and unsatisfying, and it
invites quietly moving the bar. So the calibration reports what moving it would buy:

| Accept up to | Best hit rate | Threshold |
| ------------ | ------------- | --------- |
| 1% wrong     | none exists   | —         |
| 2% wrong     | 8.0%          | 0.955     |
| 5% wrong     | 28.0%         | 0.910     |
| 10% wrong    | 43.5%         | 0.870     |

**Why it fails here, which is the part that generalises.** This corpus is academic questions, and
within a topic they are lexically near-identical while being semantically distinct. The hardest
negative found:

> How many ways are there to put 4 **distinguishable** balls into 2 **indistinguishable** boxes?
> How many ways are there to put 4 **indistinguishable** balls into 2 **distinguishable** boxes?

Two words swapped, different answers, cosine **0.9986**. No threshold separates that from a genuine
paraphrase. A workload of distinct support tickets would very likely calibrate differently — the
method transfers, this verdict does not, and the README says so.

**What makes this a result rather than a failure.** The cache was built, measured against hard
negatives with a bound that is correct at zero events, and rejected on the evidence. Reporting a
"51% hit rate" at threshold 0.85 would have been trivially easy and would have concealed a 14%
rate of confidently wrong answers.

---

## D-051 — The system prompt must not be embedded, and I did it anyway

**Status:** ACCEPTED · **Date:** 2026-09-22 · **Phase:** P6

**What happened.** `CorpusItem.prompt_text()` returns every message including the system
instruction, and the first calibration embedded that. Every item of a given task type carries the
same ~130-character instruction, so it inflated every pairwise similarity: the false-hit rate at
threshold 0.85 read **45.8%** where it belonged at **14.2%**.

The guard against exactly this already existed on the TypeScript side, in the gateway's
`embeddingText()`, with a comment spelling out the reason — _"a shared system prompt is identical
across every request in a route, so including it drags every similarity toward 1.0 and destroys the
discrimination the threshold depends on."_ The same mistake then went into the Python calibration.

**Decision.** `CorpusItem.user_prompt_text()` returns only user and assistant turns, matching
`embeddingText()`. A threshold fitted on one and applied to the other would not transfer, so the two
must agree; a test asserts the system instruction is excluded and that two items sharing an
instruction are not made similar by it.

**Why it was caught.** Not by review — by the number being implausible. A 45.8% false-hit rate at a
moderate threshold was too bad to believe, which prompted looking at the most-similar negative pair,
which showed two prompts sharing a long identical prefix. Reporting the false-hit rate at all is
what made the bug visible; a hit-rate-only report would have shown 51% and looked fine.

**The lesson.** Knowing a failure mode, documenting it, and writing a guard against it in one
language does not prevent committing it in another. The defence that worked was not the comment —
it was measuring the thing that would look wrong if the bug were present.
