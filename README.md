# Verdict

An OpenAI-compatible LLM gateway that records real traffic, replays it against candidate
prompts and models, judges quality with a human-calibrated evaluator, decides
regression-vs-noise with proper statistics, and uses that measurement to route live traffic to
the cheapest model that still clears a statistical quality floor.

> Existing tools (LangSmith, Braintrust, Promptfoo) measure LLM quality but do not close the loop
> to automatic cost-optimal routing with a proven quality guarantee. Verdict does.

**Status: Phase 0 of 8 — design and scaffold.** There are no benchmark numbers yet, and there will
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

## Development

```bash
make install      # pnpm workspace + evald virtualenv (Python 3.12)
make test         # 26 TypeScript tests, 25 Python tests
make lint         # eslint, prettier, ruff
make typecheck    # tsc --strict, mypy --strict
```

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
artifacts/       Committed benchmark output. The only source of any number.
```

## Phases

| Phase | Scope                                                          | Status |
| ----- | -------------------------------------------------------------- | ------ |
| P0    | Design, decisions, scaffold, CI, docker compose                | done   |
| P1    | Gateway: streaming proxy, adapters, traces, cost accounting    | next   |
| P2    | Benchmark corpus + deterministic replay runner                 |        |
| P3    | LLM-as-judge + calibration against human labels (Cohen's κ)    |        |
| P4    | Paired bootstrap CIs, McNemar, regression-vs-noise verdicts    |        |
| P5    | Cascade router, threshold fitting, Pareto curve                |        |
| P6    | Semantic cache with calibrated threshold; hit + false-hit rate |        |
| P7    | Dashboard + GitHub Action PR comments                          |        |
| P8    | Deploy, load test, README, demo                                |        |
