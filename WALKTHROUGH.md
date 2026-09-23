# Walkthrough — learn Verdict by running it

> **New to the project? Read [`START-HERE.md`](START-HERE.md) first.** It is six
> commands and ten minutes, in plain words. This file is the deeper version and
> assumes you've done that one.

Thirteen labs. Each one is a thing you type, a thing you see, and the reason it
matters. Work through them in order; later labs assume earlier ones.

Every command here spends **nothing** except Lab 2, which costs about **$0.0002**
and is clearly marked. Nothing needs a paid account beyond the keys already in
your `.env`.

Keep two windows open: this file on one side, a terminal on the other. Every
lab ends with **the file to go read**, because the point is not to run commands —
it is to connect what you saw to the code that caused it.

```
cd ~/Projects/flagship_project1
```

All paths below are relative to that directory.

---

## Lab 0 — Start the stack from cold

**What you'll learn:** what the five containers are and why `migrate` is one of them.

```bash
docker compose down            # stop everything, keep the data
docker compose up -d --build   # rebuild from source and start
docker compose ps -a           # -a matters: see below
```

Six services. Five stay up; one is *supposed* to exit:

| Service | Job | State when healthy |
| --- | --- | --- |
| `postgres` | every trace, every vector | Up (healthy) |
| `redis` | hot cache path | Up (healthy) |
| `migrate` | applies `db/migrations/*.sql` once, then exits | **Exited (0)** |
| `gateway` | the OpenAI-compatible API | Up (healthy) |
| `evald` | the replay / eval service | Up (healthy) |
| `dashboard` | the Next.js site | Up |

**Use `docker compose ps -a`, not `ps`.** Plain `ps` hides exited containers, so
`migrate` is invisible — and a *failed* migration looks exactly like a healthy
stack. That is the one service whose status you most need to see.

**`migrate` exiting with code 0 is success, not failure.** Check it ran:

```bash
docker compose logs migrate | tail -20
```

> **Why this service exists.** Postgres has a built-in hook that runs SQL on
> first boot. It is silently skipped if the data volume already exists. Earlier
> in this project a stale `verdict_pgdata` volume from an unrelated project meant
> the migrations never ran — and *every container reported healthy*. A one-shot
> service that must exit 0 before the gateway starts cannot fail silently.

**Always rebuild after changing source.** `docker compose up -d` alone will *not*
pick up your changes. The dashboard container in this repo once ran eight days
stale, serving an old build, with no warning anywhere.

📖 Read: `docker-compose.yml`, **D-016** in `DECISIONS.md`

---

## Lab 1 — Your first request, streaming

**What you'll learn:** the API surface, and the four headers your gateway adds.

```bash
curl -N http://localhost:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{
    "model": "openai/gpt-oss-20b",
    "stream": true,
    "messages": [{"role": "user", "content": "Say exactly: hello from verdict"}]
  }'
```

You'll see a stream of `data: {...}` lines, each a `chat.completion.chunk`, ending
with `data: [DONE]`. That is the OpenAI wire format exactly — **any OpenAI client
library can point at `localhost:8080` and work unchanged.** That compatibility is
the whole reason this is a gateway and not a library.

Now look at the headers:

```bash
curl -sN -D - -o /dev/null http://localhost:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"openai/gpt-oss-20b","stream":true,
       "messages":[{"role":"user","content":"hi"}]}' | grep -i verdict
```

```
x-verdict-request-id:    <a uuid>
x-verdict-model-served:  openai/gpt-oss-20b
x-verdict-provider:      groq
x-verdict-route:         unrouted
x-verdict-route-reason:  explicit_model
```

`route-reason` is the router telling you **why** it picked that model. You asked
for a specific model, so it obeyed: `explicit_model`. Lab 6 is about the other six
reasons.

**Trick for the rest of these labs** — set your own request id so you can find it:

```bash
curl -sN http://localhost:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -H 'x-request-id: MY-FIRST-TEST' \
  -d '{"model":"openai/gpt-oss-20b","stream":true,
       "messages":[{"role":"user","content":"What is 2+2?"}]}' > /dev/null
```

Your gateway trusts an inbound id and mints one only when absent, so a trace can
span your terminal, the gateway, and evald.

📖 Read: `apps/gateway/src/routes/chat.ts`, `apps/gateway/src/app.ts` (the `genReqId` function)

---

## Lab 2 — Follow the money 💸 *(costs ~$0.0002)*

**What you'll learn:** why every price in this codebase is an integer.

Find the trace you just created:

```bash
docker compose exec -T postgres psql -U verdict -d verdict -x -c "
SELECT model_served, provider, input_tokens, output_tokens,
       cost_usd, usage_is_final, status, ttft_ms, latency_ms
FROM traces WHERE request_id = 'MY-FIRST-TEST';"
```

Note your own `input_tokens` and `output_tokens`. Now find the price:

```bash
grep -A 8 "openai/gpt-oss-20b:" config/models.yaml
```

```yaml
    pricing:
      input: 0.075      # USD per million tokens
      output: 0.30
      verified_at: "2026-09-14"
      source: "https://console.groq.com/docs/models"
```

> Every price carries `verified_at` and `source`, and the Zod schema **rejects the
> config at boot** if either is missing. A wrong price silently corrupts every cost
> number downstream, so it is not allowed to be folklore.

Now check the arithmetic — substitute your own token counts:

```bash
python3 -c "
IN, OUT = 77, 47            # <- your numbers here
nano_in, nano_out = int(0.075*1000), int(0.30*1000)   # nano-USD per token
total = IN*nano_in + OUT*nano_out
print(f'integers: {total} nano-USD = \${total/1e9:.9f}')
print(f'floats  : \${(IN*0.075 + OUT*0.30)/1e6:.9f}')
"
```

The two disagree in the ninth decimal. **The integer version is the correct one.**
Your gateway multiplies USD-per-million-tokens by 1000 to get whole nano-USD per
token, and never touches a float. Over a long stream, float error compounds; integer
arithmetic cannot drift.

📖 Read: `apps/gateway/src/cost/meter.ts`, `packages/shared/src/config/models.ts`

---

## Lab 3 — Hang up mid-stream

**What you'll learn:** what `usage_is_final` is for, and the single hardest bug in this project.

Ask for a long answer, then kill the connection after one second:

```bash
curl -sN --max-time 1 http://localhost:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -H 'x-request-id: HANGUP-TEST' \
  -d '{"model":"openai/gpt-oss-20b","stream":true,
       "messages":[{"role":"user","content":"Write a 900 word essay about bridges."}]}' \
  > /dev/null 2>&1
sleep 3
docker compose exec -T postgres psql -U verdict -d verdict -x -c "
SELECT status, output_tokens, cost_usd, usage_is_final, latency_ms
FROM traces WHERE request_id = 'HANGUP-TEST';"
```

```
status         | 499
output_tokens  | 0
cost_usd       | 0.00000000
usage_is_final | f
latency_ms     | ~1000
```

Read those five fields carefully:

- **`499`** — the code for "client closed the request". Your gateway saw you leave.
- **`output_tokens 0`, `cost_usd 0`** — the upstream call was **aborted**. Groq
  stopped generating. Nobody paid for tokens nobody read.
- **`usage_is_final = f`** — the important one. It does **not** claim the cost was
  zero. It says *"this figure is unconfirmed."* The provider never sent a final
  usage report, so the number is a floor, not a fact.

Prove the flag is load-bearing:

```bash
docker compose exec -T postgres psql -U verdict -d verdict -c "
SELECT usage_is_final, count(*), round(sum(cost_usd), 6) AS usd
FROM traces GROUP BY 1 ORDER BY 1;"
```

You can now answer *"how much of my billing data do I actually trust?"* — and there
is a partial index built for exactly that question:

```bash
cat db/migrations/0002_usage_finality.sql
```

> **The bug that makes this lab worth doing.** The obvious way to detect a
> disconnect is to listen on the *request* object. That is wrong: an
> `IncomingMessage` fires `close` when the request **body** finishes being read —
> which for a POST is immediately. Every streaming request looked like an instant
> disconnect, cancelled its own upstream call, and returned an empty HTTP 200 with
> nothing in the logs. **Every test using Fastify's `inject()` passed.** Only the
> tests that open a real socket caught it. The fix listens on `reply.raw` and
> guards with `writableEnded`.

📖 Read: `apps/gateway/src/routes/chat.ts` (search `writableEnded`), **D-020** in `DECISIONS.md`

---

## Lab 4 — Dig up your own worst bug

**What you'll learn:** that your database is a forensic record, and how to read it.

```bash
docker compose exec -T postgres psql -U verdict -d verdict -c "
SELECT date_trunc('day', created_at)::date AS day,
       count(*) AS total,
       count(*) FILTER (WHERE status >= 500) AS errors,
       count(*) FILTER (WHERE status = 499)  AS disconnects
FROM traces GROUP BY 1 ORDER BY 1;"
```

One day will have hundreds of errors. Ask what they were:

```bash
docker compose exec -T postgres psql -U verdict -d verdict -c "
SELECT status, error_kind, count(*)
FROM traces WHERE status >= 400
GROUP BY 1,2 ORDER BY 3 DESC;"
```

```
 503 | breaker_open |   339
 429 | rate_limited |     7
```

**Seven rate limits caused 339 refused requests.** The circuit breaker's threshold
was 5 failures. Groq sent 7 × HTTP 429, the breaker opened, and it then refused the
entire rest of the run instantly — without calling upstream at all.

Cross-check it against the written record:

```bash
grep -n "339" DECISIONS.md
```

The database and the decision log agree to the row.

Now read the fix, and the reasoning written next to it:

```bash
sed -n '69,84p' apps/gateway/src/providers/types.ts
```

> A circuit breaker exists to stop calling a **broken** service. HTTP 429 does not
> mean broken — it means healthy and asking you to slow down. The correct responses
> are opposite: one is *stop*, the other is *pace yourself*.

```bash
grep -n "countsTowardBreaker" apps/gateway/src/providers/anthropic.ts
cd apps/gateway && pnpm vitest run src/providers/classification.test.ts; cd ../..
```

📖 Read: `apps/gateway/src/providers/types.ts`, `apps/gateway/src/resilience/breaker.ts`, **D-027** and **D-028**

---

## Lab 5 — Watch the router refuse to save you money

**What you'll learn:** the single most important design rule in the project.

```bash
sed -n '1,40p' apps/gateway/src/router/select.ts
```

Count the `return` statements in `selectModel`. There are seven. **Six of them
return `safe_default`.** Only one — `policy_route` — hands out a cheaper model.

Now trigger it. Ask for the magic model name `verdict-auto` with no policy loaded:

```bash
curl -s -D - -o /dev/null http://localhost:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"verdict-auto","max_tokens":5,
       "messages":[{"role":"user","content":"hi"}]}' | grep -i verdict
```

```
x-verdict-model-served:  claude-opus-5
x-verdict-route-reason:  router_off
```

**It served the most expensive model in the ladder.** Check what that costs:

```bash
grep -n "safe_default" config/models.yaml
docker compose exec -T postgres psql -U verdict -d verdict -c "
SELECT model_served, input_tokens + output_tokens AS tokens, cost_usd
FROM traces WHERE cost_usd > 0 ORDER BY created_at DESC LIMIT 2;"
```

Opus costs roughly **79× more per token** than the cheap rung. That is the price of
the rule, and the rule is deliberate:

> **Ambiguity resolves toward quality.** No policy, no route hint, an unknown route,
> a disabled router — every one of those serves a strong model. Cost optimisation
> happens *only* where there is positive evidence it is safe.

The failure mode this prevents: a config typo, a missing file, or an unrecognised
route silently downgrading production traffic to a cheap model. **Fail expensive,
never fail cheap.**

📖 Read: `apps/gateway/src/router/select.ts`, `apps/gateway/src/router/policy.ts`, DESIGN.md §8.2

---

## Lab 6 — The four verdicts, with the truth known in advance

**This is the most important lab.** It is the heart of the project.

The problem: *"no significant difference"* means two opposite things. Either you
measured carefully and they really are the same (**switch and save money**), or you
didn't measure enough to tell (**you know nothing**). Most tools report both the
same way. Yours doesn't.

Feed the statistics engine data where **you** control the truth:

```bash
cd apps/evald
.venv/bin/python - <<'PY'
import random
from evald.stats.verdict import compare_runs

def fake(n, p_base, p_cand, seed):
    """Two models on n questions. I set the TRUE pass rates."""
    r = random.Random(seed)
    return ([r.random() < p_base for _ in range(n)],
            [r.random() < p_cand for _ in range(n)])

print("The candidate is TRULY 8 points WORSE (0.82 -> 0.74).")
print("Same truth every time. Only the sample size changes.\n")
for n in (60, 200, 667, 2000):
    a, b = fake(n, 0.82, 0.74, seed=7)
    v = compare_runs(a, b, margin=0.03)
    print(f"  n={n:<5} {v.outcome:<13} effect {v.effect:+.3f}  "
          f"CI [{v.ci_low:+.3f},{v.ci_high:+.3f}]  resolution ±{v.minimum_detectable_effect:.3f}")

print("\nThe two models are TRULY IDENTICAL (0.80 vs 0.80).")
print("Watch INCONCLUSIVE become EQUIVALENT once there is enough data.\n")
for n in (60, 200, 667, 2000, 5000):
    a, b = fake(n, 0.80, 0.80, seed=3)
    v = compare_runs(a, b, margin=0.03)
    print(f"  n={n:<5} {v.outcome:<13} effect {v.effect:+.3f}  "
          f"CI [{v.ci_low:+.3f},{v.ci_high:+.3f}]  resolution ±{v.minimum_detectable_effect:.3f}")
PY
cd ../..
```

Three things to notice in the output:

1. **At n=60 the effect estimate is wildly wrong** — around −0.18 when the truth is
   −0.08. Small samples don't just add noise, they *exaggerate*. Only by n=667 does
   the estimate land near the truth.
2. **In the identical case it never reaches EQUIVALENT until n≥2000.** It is not
   being difficult; an interval wider than the ±0.03 margin *cannot fit inside it*,
   however the estimate falls. The `can_demonstrate_equivalence` field says so
   explicitly.
3. **One run says INCONCLUSIVE while excluding zero.** The difference is real but
   the interval straddles the margin — statistically significant, practically
   unknown. That is exactly why the verdict is driven by the confidence interval
   rather than the p-value.

📖 Read: `apps/evald/src/evald/stats/verdict.py` — read the module docstring first, it is the argument in full

---

## Lab 7 — Prove the statistics are calibrated

**What you'll learn:** the number that makes the whole project defensible.

Run 200 independent experiments on two **genuinely identical** models:

```bash
cd apps/evald
.venv/bin/python - <<'PY'
import random, collections
from evald.stats.verdict import compare_runs

def fake(n, pa, pb, seed):
    r = random.Random(seed)
    return ([r.random() < pa for _ in range(n)], [r.random() < pb for _ in range(n)])

c, worst = collections.Counter(), 0.0
for s in range(200):
    a, b = fake(60, 0.80, 0.80, seed=s)
    v = compare_runs(a, b, margin=0.03, iterations=2000)
    c[v.outcome] += 1
    worst = max(worst, abs(v.effect))

print("200 runs, two models that are TRULY IDENTICAL, n=60 each:\n")
for k, n in c.most_common():
    print(f"  {k:<13} {n:>3}/200  ({n/2:.1f}%)")
print(f"\n  Largest fake 'difference' produced by pure luck: {worst:+.3f}")
PY
cd ../..
```

Two numbers matter:

- **False alarms ≈ 4%** against a nominal α of 5%. The statistics are *calibrated*,
  not hand-waved. You can state this in an interview and it is checkable in 20 seconds.
- **EQUIVALENT: 0 out of 200.** On truly identical models at n=60, your system never
  once said *"these are the same, go save money."* The dangerous error isn't rare —
  it is structurally impossible at that sample size.

And the punchline: noise alone manufactured apparent gaps up to **±0.28**. Your real
measured Haiku-vs-Sonnet gap was **0.000**. At n=60 you cannot tell a genuine
8-point regression from luck, which is why that comparison had to be INCONCLUSIVE.

```bash
cat artifacts/verdict-claude-haiku-4-5-vs-claude-sonnet-5.json
```

📖 Read: `apps/evald/src/evald/stats/bootstrap.py`, `mcnemar.py`, **D-035** in `DECISIONS.md`

---

## Lab 8 — See why the cache was rejected

**What you'll learn:** how to kill your own feature on the evidence.

A semantic cache reuses an old answer when someone asks the same thing in different
words. Whether that is safe is an empirical question:

```bash
cd apps/evald
.venv/bin/python - <<'PY' 2>&1 | grep -vi "warn\|hugging"
from evald.cache.embed import LocalEmbedder
import math
E = LocalEmbedder()
cos = lambda a,b: sum(x*y for x,y in zip(a,b)) / (
    math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(y*y for y in b)))

pairs = [
 ("SAME  ", "How do I reset my password?",
            "What's the process for resetting my password?"),
 ("SAME  ", "Summarise this refund policy for a customer.",
            "Give me a customer-facing summary of the refund policy."),
 ("DIFFER", "How many ways to put 4 distinguishable balls into 2 indistinguishable boxes?",
            "How many ways to put 4 indistinguishable balls into 2 distinguishable boxes?"),
 ("DIFFER", "What is 17 percent of 3400?", "What is 17 percent of 3500?"),
 ("DIFFER", "List the countries that ARE in the European Union.",
            "List the countries that are NOT in the European Union."),
]
print(f"{'cosine':>8}  truth   case")
for kind, a, b in pairs:
    va, vb = E.embed_texts([a, b])
    print(f"{cos(va,vb):>8.4f}  {kind}  {a[:52]}")
PY
cd ../..
```

The balls-and-boxes pair scores **~0.998** — *higher* than either genuine paraphrase
(~0.94). Two words swapped, completely different answers, and the embedding model
cannot tell. **There is no threshold between 0.998 and 0.94.** Any cutoff that
catches real paraphrases also serves wrong answers.

Now the full calibration, measured on 200 paraphrases against 600 hard negatives:

```bash
python3 -c "
import json
d = json.load(open('artifacts/cache-calibration.json'))
print(f\"{'thresh':>7} {'hit%':>7} {'wrong%':>8} {'upper bound':>12}\")
for p in d['points']:
    if round(p['threshold'],3) in (0.85,0.90,0.95,0.975,1.0):
        print(f\"{p['threshold']:>7.3f} {p['hit_rate']*100:>7.1f} \"
              f\"{p['false_hit_rate']*100:>8.2f} {p['false_hit_ci_high']*100:>11.2f}%\")
print()
print('tolerance:', d['max_false_hit_rate'], '| chosen threshold:', d['chosen_threshold'])
print('negatives needed to prove 1%:', d['min_negatives_required'])
for n in d['notes']: print(' -', n)
"
```

`chosen_threshold` is **`null`**. No setting is both safe and useful, so **your
gateway ships no cache.**

Two details that make this a result rather than a failure:

- **The negatives are hard by construction** — each item paired with its *nearest
  different neighbour*. Random negatives are trivially separable and would flatter
  any threshold you like.
- **The bound is Clopper-Pearson, not bootstrap.** A bootstrap reports exactly 0.00%
  when it observes zero errors — which would let a completely untested threshold look
  proven. Since the threshold gets chosen *on that upper bound*, the cache would have
  been loosened on an artefact of the method. Clopper-Pearson turns 0/150 into
  *"up to 2.43%"*, and gives you the rule: proving <1% needs **≥368** hard negatives.

📖 Read: `apps/evald/src/evald/cache/calibrate.py`, `stats/proportion.py`, **D-046**, **D-047**, **D-051**

---

## Lab 9 — The spend cap, before you spend

**What you'll learn:** why you are shown the bill before the work runs.

```bash
make plan CAP=30
```

```
  model                      calls      in tok     out tok         USD
  claude-opus-5               1900     316,675   1,084,000     28.6834
  ...
  TOTAL                      13300   2,216,725   7,588,000     58.6255
  spend cap: $30.00  ->  EXCEEDS CAP

Nothing has been spent.
```

A full replay of all 1,500 items across all 7 models costs **~$59**. The runner
refuses to start because you set a $30 cap.

Three things to notice:

1. **The header says `estimate, not a measured number`.** The projection uses
   measured output-token medians where they exist and stated assumptions elsewhere.
   An earlier version under-projected by 2.1× ($0.2366 projected, $0.4925 actual)
   because it guessed 150–300 output tokens when reality was 440–953.
2. **`make bench` refuses to run without `CAP=`.** Not a default — a refusal.
3. **Re-runs are free.** Responses are cached on disk, content-addressed by
   `(model, prompt hash, params)`. The first run costs money; every subsequent run
   costs nothing and produces byte-identical results.

Your corpus:

```bash
wc -l corpus/items.jsonl
head -1 corpus/items.jsonl | python3 -m json.tool
```

1,500 items. Each carries `slug`, `source_dataset`, `source_license`, `split`, and
`ground_truth` (which is `null` for free-form items — those need a judge).

📖 Read: `apps/evald/src/evald/replay/plan.py`, `budget.py`, `cache.py`, **D-042**

---

## Lab 10 — The dashboard

**What you'll learn:** how the site stays honest about what it doesn't know.

```bash
open http://localhost:3000
```

Visit all five pages. **Two of them are deliberately empty:**

| Page | State | Why |
| --- | --- | --- |
| `/` | overview | — |
| `/pareto` | **empty** | no routing policy fitted — needs a full judged replay |
| `/spend` | real data | from the committed trace sample |
| `/compare` | real data | your two verdict artifacts |
| `/traces` | real data | stratified sample, replayable |

The empty states are the point. `/pareto` tells you exactly which command would
produce the data. The dashboard reads **committed JSON files only** — never a
database — so it builds statically, deploys with no connection string, and cannot
break during a demo.

```bash
sed -n '1,20p' apps/dashboard/lib/artifacts.ts
```

Check `/compare` closely. The confidence interval is drawn **to scale against the
±3% margin band**, so you can see at a glance that the Haiku-vs-Sonnet interval is
far too wide to fit inside it. That is the Lab 6 lesson, rendered.

> **The bug hiding in here.** The Dockerfile copied `apps/dashboard` into the build
> stage and nothing else — but the dashboard reads `artifacts/` at **build** time.
> Inside the image every loader returned `null` and every page rendered its empty
> state. The build succeeded, the container reported healthy, and `make dash` on the
> host worked perfectly. **The containerised dashboard had never displayed a single
> number.** An empty state is indistinguishable from a missing measurement — which
> is correct for this project, and is exactly why it hid a packaging bug for a whole
> phase.

📖 Read: `apps/dashboard/lib/artifacts.ts`, `apps/dashboard/components/ui.tsx` (the `IntervalBar`), **D-052**, **D-056**

---

## Lab 11 — Webhook signature verification

**What you'll learn:** how to accept input from the public internet.

```bash
cd apps/ghapp && pnpm vitest run src/signature.test.ts; cd ../..
sed -n '1,60p' apps/ghapp/src/signature.ts
```

Four properties, and each one is a specific attack:

| Property | Attack it stops |
| --- | --- |
| HMAC-SHA256 over the **raw bytes** | re-serialising JSON changes the bytes and breaks the signature |
| `timingSafeEqual`, never `===` | a timing side-channel leaks the signature byte by byte |
| **shape-validated before** comparing | `timingSafeEqual` throws on a length mismatch; an attacker gets a different error |
| bounded, TTL'd delivery log | replaying a captured valid request |

The ordering in that third row matters and is easy to get wrong: you must check the
signature *looks* like a signature before you compare it, or the error you return
tells the attacker something.

📖 Read: `apps/ghapp/src/signature.ts`, `apps/ghapp/src/index.ts`

---

## Lab 12 — Read the test suite as a specification

**What you'll learn:** the fastest way to understand any part of this codebase.

```bash
grep -oE '^  it\("[^"]+' apps/gateway/src/routes/chat.test.ts | sed 's/^  it("//'
```

The test names *are* the spec:

```
streams rather than buffering: chunks arrive before the upstream finishes
aborts the upstream call so we stop paying for unread tokens
records the truncation and marks usage as unconfirmed
retries a 500 before any byte reaches the client, then succeeds
does not retry a 400, because it will fail identically every time
opens the breaker after repeated failures and then refuses without calling upstream
serves the safe default for a route the policy never saw
keeps serving when the trace sink is failing
refuses to boot when the router is on but no policy is configured
```

Each is a claim you can defend. Run everything:

```bash
make test        # 254 TypeScript + 413 Python
make typecheck   # TS strict + mypy strict
make lint
```

The tests that caught the disconnect bug open a **real HTTP server** that speaks
Anthropic's SSE dialect, and can destroy the socket mid-stream on command:

```bash
sed -n '1,50p' apps/gateway/src/testing/mock-provider.ts
```

> That file exists because `inject()` tests — which don't use a real socket — passed
> while the gateway was completely broken in production.

📖 Read: `apps/gateway/src/testing/mock-provider.ts`

---

## Where to go next

You have now touched every shipped part of the system. Two things are **built and
tested but never run for real**, and both cost something other than money:

- **Judge calibration** (`make label PAIRS=...`) — needs ~200 hand labels from you,
  4–6 hours. Produces Cohen's kappa: does the AI judge agree with a human?
- **Routing policy fit** (`make bench CAP=...` then `evald fit`) — needs a full
  judged replay, ~$59 at current prices, or less on a sampled subset.

Until those run, **no cost-saving figure is claimed anywhere in this repo**, and
that is on purpose.

### The one file to read before an interview

```bash
grep -c "^## D-" DECISIONS.md
```

Each one states what was chosen, what was rejected, and why. That file is the
project.
