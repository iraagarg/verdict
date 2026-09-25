# Deploying Verdict

Two services, three providers, all on free tiers. About 30 minutes end to end.

| What                | Where       | Free tier           |
| ------------------- | ----------- | ------------------- |
| Postgres + pgvector | **Neon**    | 0.5 GB storage      |
| Gateway             | **Railway** | $5 credit/month     |
| Dashboard           | **Vercel**  | generous hobby tier |

**No Redis.** Upstash was in the original plan, and this phase found the gateway has no Redis
client at all: `REDIS_URL` was validated at boot and never connected to. Redis's only intended
consumer was the semantic cache, which P6 measured and rejected (**D-046**, **D-047**). Requiring
it would mean provisioning a service to satisfy a schema. It is now optional (**D-059**).

**`apps/evald` is deliberately not deployed.** It exposes only `/health` and `/ready`, nothing
calls it, and every capability it has runs through its CLI against local files. Deploying it would
mean paying for a process whose entire production behaviour is answering its own health check. See
**D-058**.

Until you set the secrets below, the Deploy workflow runs, reports which targets are configured,
and deploys nothing. Main stays green. Add secrets one at a time and each target switches on.

---

## 1. Neon — Postgres with pgvector

1. Sign up at [neon.tech](https://neon.tech) and create a project. Pick the region nearest you.
2. On the dashboard, copy the **pooled** connection string. It looks like
   `postgresql://user:pass@ep-xxx-pooler.region.aws.neon.tech/neondb?sslmode=require`.
3. Enable pgvector — Neon ships the extension, `0003_vectors.sql` turns it on:

   ```bash
   export DATABASE_URL='<your neon string>'
   ./tools/migrate.sh
   ```

   You should see three migrations apply and `migrations complete`.

4. Prove it took:

   ```bash
   psql "$DATABASE_URL" -c "\dt"
   psql "$DATABASE_URL" -c "SELECT extname FROM pg_extension WHERE extname='vector';"
   ```

   Expect the `traces`, `routes` and `semantic_cache_entries` tables, and one row saying `vector`.

> **Use the pooled string, not the direct one.** Railway restarts containers and the gateway opens
> a connection pool on boot (`PG_POOL_MAX`, default 10). Neon's direct endpoint caps connections
> much lower than the pooler, and you will hit it under any real traffic.

---

## 2. Railway — the gateway

1. Sign up at [railway.app](https://railway.app), **New Project → Deploy from GitHub repo**, pick
   `iraagarg/verdict`.
2. Railway reads [`railway.json`](railway.json): it builds `apps/gateway/Dockerfile` and health-checks
   `/health`. Name the service **`gateway`** — the deploy workflow refers to it by that name.
3. Set the variables. **The gateway refuses to start if any of these are missing or malformed**, so
   a typo here fails the deploy rather than surfacing as a broken request an hour later:

   | Variable               | Value                       |
   | ---------------------- | --------------------------- |
   | `DATABASE_URL`         | your Neon **pooled** string |
   | `COST_CAP_USD_PER_DAY` | `5.00`                      |
   | `ANTHROPIC_API_KEY`    | your key, or leave unset    |
   | `GROQ_API_KEY`         | your key, or leave unset    |
   | `NODE_ENV`             | `production`                |
   | `LOG_LEVEL`            | `info`                      |

   At least one provider key is required — the schema enforces it.

4. Railway assigns a domain. Check it:

   ```bash
   curl -s https://<your-app>.up.railway.app/health
   ```

> **`GATEWAY_PORT` is not in that table on purpose.** Railway injects `PORT`. If the gateway does
> not pick that up, set `GATEWAY_PORT=${{PORT}}` in Railway's variable editor, which expands
> Railway's own value.

---

## 3. Vercel — the dashboard

1. Sign up at [vercel.com](https://vercel.com), **Add New → Project**, import `iraagarg/verdict`.
2. Vercel reads [`vercel.json`](vercel.json). Leave the root directory as the repo root — **not**
   `apps/dashboard`. The dashboard reads `artifacts/` at build time and bakes the numbers into the
   static output (**D-052**), so it needs the whole repo present. Pointing Vercel at the app
   directory alone reproduces **D-056**: the site builds fine and every page renders empty.
3. Deploy. No environment variables — the dashboard has no runtime dependencies, no database and no
   secrets. That is the whole point of reading committed artifacts.

---

## 4. Automatic deploys

Add these under **Settings → Secrets and variables → Actions** on GitHub.

### Secrets

| Secret              | Where to get it                                           |
| ------------------- | --------------------------------------------------------- |
| `DATABASE_URL`      | Neon pooled string — used to run migrations before deploy |
| `RAILWAY_TOKEN`     | Railway → Account Settings → Tokens                       |
| `VERCEL_TOKEN`      | Vercel → Settings → Tokens                                |
| `VERCEL_ORG_ID`     | `.vercel/project.json` after running `vercel link`        |
| `VERCEL_PROJECT_ID` | same file                                                 |

### Variables (not secrets)

| Variable      | Value                               |
| ------------- | ----------------------------------- |
| `GATEWAY_URL` | `https://<your-app>.up.railway.app` |

`GATEWAY_URL` turns on the post-deploy health check. Without it the workflow trusts Railway's word
that the deploy succeeded; with it, the run fails if the deployed service is not actually answering.
A deploy that reports success while the service 503s is not a success.

### What the workflow does

```
push to main
  └─ test        every suite, exactly what `make check` runs locally
      └─ migrate  ./tools/migrate.sh against Neon
          └─ deploy gateway   → Railway, then poll /health until it answers
          └─ deploy dashboard → Vercel
```

**Migrations run before the deploy, never after.** The alternative leaves a window where new code is
serving traffic against an old schema.

It is the same `tools/migrate.sh` that docker compose and CI use. One migration path across three
environments, so a migration that works locally cannot fail in production for a reason nobody has
seen before. CI applies the migrations twice and fails if the second pass errors, which is what
makes it safe to run them on every deploy without tracking which have already been applied.

---

## Verifying a real deployment

```bash
GATEWAY=https://<your-app>.up.railway.app

curl -s $GATEWAY/health
curl -s $GATEWAY/ready

curl -N $GATEWAY/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"openai/gpt-oss-20b","stream":true,
       "messages":[{"role":"user","content":"Say hello"}]}'
```

Then confirm the trace landed:

```bash
psql "$DATABASE_URL" -c \
  "SELECT model_served, input_tokens, output_tokens, cost_usd, usage_is_final
   FROM traces ORDER BY created_at DESC LIMIT 1;"
```

If the row is there with a non-zero cost and `usage_is_final = t`, the whole path works: request in,
stream out, tokens counted, cost computed, trace persisted.

---

## Costs, honestly

Everything above fits in free tiers at portfolio traffic. Two things to watch:

- **Railway's $5/month credit** is consumed by uptime, not requests. One always-on service is
  roughly $3–5/month. If you exceed it, Railway sleeps the service and the first request after a
  sleep is slow.
- **Neon sleeps an idle database.** The first request after a sleep pays a cold start of a second
  or two. That is a property of the free tier, not of the gateway, and it will not show up in
  `artifacts/loadtest.json`, which is measured against a local Postgres.

Your provider keys are the real cost. `COST_CAP_USD_PER_DAY` is the backstop; set it low.
