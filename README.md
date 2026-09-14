# Verdict

An OpenAI-compatible LLM gateway that records real traffic, replays it against candidate
prompts and models, judges quality with a human-calibrated evaluator, decides
regression-vs-noise with proper statistics, and uses that measurement to route live traffic to
the cheapest model that still clears a statistical quality floor.

> Existing tools (LangSmith, Braintrust, Promptfoo) measure LLM quality but do not close the loop
> to automatic cost-optimal routing with a proven quality guarantee. Verdict does.

**Status: Phase 1 of 8 — the gateway is live.** There are no benchmark numbers yet, and there will
be none in this README until `make bench` produces them into committed artifacts. A number that is
not in `artifacts/` does not exist.

- [DESIGN.md](DESIGN.md) — problem, architecture, schema, API contract, routing algorithm,
  evaluation methodology, failure modes, threats to validity
- [DECISIONS.md](DECISIONS.md) — every engineering decision with its rejected alternatives

## Quick start

```bash
cp .env.example .env          # then set at least one provider key
make up                       # postgres, redis, migrations, gateway, evald, dashboard
curl -s localhost:8080/health
```

| Service   | URL                          |
| --------- | ---------------------------- |
| gateway   | http://localhost:8080/health |
| evald     | http://localhost:8000/health |
| dashboard | http://localhost:3000        |

`make down` stops everything and drops the volume. `make help` lists every target.

## Using the gateway

Point any OpenAI client at it — change `baseURL` and nothing else.

```bash
curl -N http://localhost:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "Explain SSE in one sentence."}],
    "stream": true,
    "stream_options": {"include_usage": true}
  }'
```

`model` accepts any id in [config/models.yaml](config/models.yaml), or `verdict-auto` to let Verdict
choose (P1 resolves that to the configured `safe_default`; P5 replaces it with a fitted policy).

Response headers carry what Verdict did, so the body stays a byte-for-byte OpenAI shape:

| Header                     | Meaning                                                      |
| -------------------------- | ------------------------------------------------------------ |
| `x-verdict-request-id`     | Propagated to every log line and the trace row               |
| `x-verdict-model-served`   | The rung that actually served the request                    |
| `x-verdict-provider`       | `anthropic` / `openai` / `groq`                              |
| `x-verdict-cost-usd`       | Exact cost, 8dp (non-streaming)                              |
| `x-verdict-usage-final`    | Whether the provider confirmed its token counts              |
| `x-verdict-dropped-params` | Parameters the served model rejects (see DECISIONS.md D-010) |

## Development

```bash
make install      # pnpm workspace + evald virtualenv (Python 3.12)
make test         # 162 TypeScript tests, 250 Python tests
make lint         # eslint, prettier, ruff
make typecheck    # tsc --strict, mypy --strict
```

## Benchmarking

```bash
make corpus                 # rebuild the frozen corpus from public datasets (free)
make plan CAP=30            # show projected cost. Spends nothing.
make pilot CAP=1 N=200      # measure whether the gradable slice discriminates
make bench CAP=30           # run it; writes a versioned artifact

make label PAIRS=200        # hand-label pairs for judge calibration (free)
make calibrate PAIRS=200    # judge them, write calibration_report.json
```

`make bench` refuses to start without a spend cap, prints a projection before spending anything, and
aborts hard if the cap is reached — writing a partial artifact marked `aborted_budget` so a truncated
run can never be mistaken for a complete one. Every result is written to a content-addressed cache,
so a second run makes zero paid calls.

The corpus is **1,500 items**: 1,200 gradable (GSM8K, MMLU-Pro — scored by exact match, no judge
needed) and 300 free-form (summarisation, long-form QA, support replies — these need the judge).
Every item records its source dataset, id and licence.

## Layout

```
apps/gateway     TypeScript + Fastify. OpenAI-compatible proxy, provider adapters,
                 trace recording, token and cost accounting.            (P1)
apps/evald       Python + FastAPI. Replay runner, LLM-as-judge, calibration,
                 significance testing, routing-policy fitting.       (P2-P5)
apps/dashboard   Next.js. Pareto curve, per-route spend, trace explorer.  (P7)
packages/shared  Shared TypeScript types and Zod schemas.
config/          Model ladder and pricing. Every price cites its source.
db/migrations/   Plain SQL, applied by the compose `migrate` service.
corpus/          The frozen benchmark corpus. Never edited; corrections get a new slug.
artifacts/       Committed benchmark output. The only source of any number.
```

## Phases

| Phase | Scope                                                          | Status |
| ----- | -------------------------------------------------------------- | ------ |
| P0    | Design, decisions, scaffold, CI, docker compose                | done   |
| P1    | Gateway: streaming proxy, adapters, traces, cost accounting    | done   |
| P2    | Benchmark corpus + deterministic replay runner                 | done   |
| P3    | LLM-as-judge + calibration against human labels (Cohen's κ)    | built  |
| P4    | Paired bootstrap CIs, McNemar, regression-vs-noise verdicts    | next   |
| P5    | Cascade router, threshold fitting, Pareto curve                |        |
| P6    | Semantic cache with calibrated threshold; hit + false-hit rate |        |
| P7    | Dashboard + GitHub Action PR comments                          |        |
| P8    | Deploy, load test, README, demo                                |        |
