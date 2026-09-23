# Start here — your project explained, hands on

**Part 1** — six terminal commands, ten minutes. What the system does.
**Part 2** — the dashboard at `localhost:3000`, page by page. What it found.

---

# Part 1 — six steps in the terminal

Six commands. Copy one, paste it, read what comes back. About ten minutes.

This whole file costs about **$0.0002** to run. Nothing here can break anything.

First, open a terminal and go to the project:

```bash
cd ~/Projects/flagship_project1
```

Make sure it's running:

```bash
docker compose up -d
```

---

## Step 1 — Is it alive?

```bash
curl -s localhost:8080/health
```

```json
{"status":"ok","service":"gateway","uptime_s":37368}
```

**What this is:** your gateway is a small web server. It sits between an app and
the AI companies. This is it saying "I'm awake."

---

## Step 2 — Ask it a question

```bash
curl -s localhost:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -H 'x-request-id: STEP2' \
  -d '{"model":"openai/gpt-oss-20b",
       "messages":[{"role":"user","content":"What is the capital of France? One word."}]}'
```

You get back JSON containing `"content": "Paris"`.

**What just happened:** your gateway took your question, sent it to Groq, got the
answer, and handed it back to you.

**Why it matters:** that request format is *exactly* OpenAI's format. Any app
already written for OpenAI can point at your gateway and work with no changes.
That is why this is a **gateway** and not a library — you don't have to rewrite
anything to use it.

> `x-request-id: STEP2` is just a name tag so we can find this request again in
> Step 3. You can put any text there.

---

## Step 3 — What did it write down?

```bash
docker compose exec -T postgres psql -U verdict -d verdict -x -c \
  "SELECT model_served, input_tokens, output_tokens, cost_usd, latency_ms
   FROM traces WHERE request_id = 'STEP2';"
```

```
model_served  | openai/gpt-oss-20b
input_tokens  | 81
output_tokens | 23
cost_usd      | 0.00001298
latency_ms    | 558
```

**What this is:** every single request gets saved to a database. Which model
answered, how many words went in and out, what it cost, how long it took.

**Why it matters:** you cannot improve what you do not measure. Everything later
in this project is built on top of this table.

---

## Step 4 — Let the gateway choose the model

Instead of naming a model, say `verdict-auto` — meaning *"you pick."*

```bash
curl -s -D - -o /dev/null localhost:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"verdict-auto","max_tokens":5,
       "messages":[{"role":"user","content":"hi"}]}' | grep -i x-verdict
```

```
x-verdict-model-served:  claude-opus-5
x-verdict-route-reason:  router_off
x-verdict-cost-usd:      0.00016500
```

**It picked the most expensive model in the list.**

**Why?** Look at `route-reason`: `router_off`. That means *"I have no proof that
a cheaper model is good enough here."*

**This is the single most important rule in your project:**

> When unsure, pick the expensive one.

Think about the opposite rule. If a config file had a typo, or a policy file was
missing, a "when unsure, pick the cheap one" system would quietly downgrade your
real users to a worse model and nobody would notice. Your system fails the other
way — it costs more, and that is a mistake you can see on a bill instead of a
mistake you discover from angry customers.

---

## Step 5 — See what that safety costs

```bash
docker compose exec -T postgres psql -U verdict -d verdict -c \
  "SELECT model_served, input_tokens + output_tokens AS tokens, cost_usd
   FROM traces WHERE created_at > now() - interval '10 minutes' AND cost_usd > 0
   ORDER BY created_at;"
```

```
 openai/gpt-oss-20b |    104 | 0.00001298
 claude-opus-5      |     13 | 0.00016500
```

Work out the price per word:

- `gpt-oss-20b` — **$0.000000125** per token
- `claude-opus-5` — **$0.000012692** per token

**The expensive model costs about 100× more.**

So now you have the question this entire project exists to answer:

> **Could we have used the cheap one?**

---

## Step 6 — The answer, and the whole point

```bash
python3 -c "
import json
d = json.load(open('artifacts/verdict-claude-haiku-4-5-vs-claude-sonnet-5.json'))
print('Scores: ', d['baseline'], f\"{d['baseline_rate']*100:.1f}%\")
print('        ', d['candidate'], f\"{d['candidate_rate']*100:.1f}%\")
print('Answer: ', d['outcome'])
print('Tested on', d['n'], 'questions')
print('Accurate to only +/-', round(d['minimum_detectable_effect']*100), 'points')
"
```

```
Scores:  claude-haiku-4-5 81.7%
         claude-sonnet-5  81.7%
Answer:  INCONCLUSIVE
Tested on 60 questions
Accurate to only +/- 10 points
```

Read that slowly, because it is the cleverest part of your project.

Sonnet costs **twice** as much as Haiku. They scored **exactly the same**. Every
instinct says *switch to Haiku and save half your money.*

**Your system says no.** Here is why.

You only tested 60 questions. With 60 questions, your measurement is accurate to
about ±10 points. So a real gap of 8 points — Haiku genuinely being worse — would
have produced **exactly the same result you are looking at.** You cannot tell the
difference between *"they are the same"* and *"I didn't test enough to notice."*

So the system reports **INCONCLUSIVE**, which means: *get more data, don't switch yet.*

### Why this is the interesting part

Most tools would print **"no significant difference"** here — and a team would
read that as "great, they're the same" and switch. That phrase hides two
completely opposite situations:

| What it could mean | What you should do |
| --- | --- |
| We measured carefully and they really are the same | Switch. Save the money. |
| We didn't measure enough to tell | Do **not** switch. You know nothing. |

Your system splits those into two different answers — **EQUIVALENT** and
**INCONCLUSIVE** — so they can never be confused. That one distinction is the
thesis of the whole project.

---

# Part 2 — the dashboard, page by page

```bash
open http://localhost:3000
```

Five pages across the top. Here is what each one is for.

> **One rule governs this whole site:** every number on it is read from a file in
> `artifacts/`. Nothing is typed by hand, nothing comes from a live database. If a
> measurement has not been run, the page says so instead of showing a number. You
> will see that happen on `/pareto`.

---

## Page 1 — `/` Overview

The summary of everything you have actually proven.

**"Measured results"** — two coloured boxes:

| | |
| --- | --- |
| 🟡 **INCONCLUSIVE** `haiku → sonnet` | Effect 0.00pp, n=60 — *can't tell, need more data* |
| 🟢 **IMPROVEMENT** `gpt-oss-20b → haiku` | Effect 16.07pp, n=56 — *haiku is genuinely better* |

Those two boxes together are the proof your system works. Given a **real** gap it
committed immediately (green). Given **no visible** gap it refused to conclude
(yellow) rather than guessing. A system that only ever said "inconclusive" would be
useless; a system that always concluded would be dangerous. Yours does both correctly.

**"Model pass rates"** — how many questions each model got right. Haiku and Sonnet
both 81.7%, the cheap open model 63.3%.

**"Semantic cache"** — says *"Chosen threshold: none — unsafe."* You built a
feature, measured it, and it failed. The dashboard reports that instead of hiding it.

**"Traffic"** — 241 requests sampled out of 1,145 in the database, $0.50 spent.

---

## Page 2 — `/pareto` Cost vs quality

**The top half is empty, and that is correct.**

> *"No routing policy has been fitted."*

It then tells you exactly which two commands would fill it in. This is what an honest
empty state looks like — not a spinner, not a fake chart, not zeros. It says what is
missing and how to produce it.

**The bottom half is the cache table**, and it is worth reading carefully:

| Threshold | Hit rate | False hits | Safe? |
| --- | --- | --- | --- |
| 0.800 | 65.0% | 42.50% | no |
| 0.850 | 51.0% | 14.17% | no |
| 0.900 | 32.0% | 3.33% | no |
| 0.950 | 12.0% | 1.33% | no |
| 1.000 | **0.0%** | 0.00% | **yes** |

**How to read it.** "Threshold" is how similar two questions must be before you reuse
an old answer. Loose at the top, strict at the bottom.

- **Loose (0.800):** saves you 65% of your calls — but **42% of those answers are wrong.**
- **Strict (1.000):** never wrong — but the hit rate is **0%**, so it does nothing at all.

Only the bottom row is "safe", and it is useless. Everything useful is unsafe. So the
answer is: **ship no cache.**

Underneath, the page prices the compromise honestly:

> accept up to 5% wrong → 28.0% hit rate
> accept up to 10% wrong → 43.5% hit rate

You could have moved your own standard to make the feature look successful. You
published the price instead.

---

## Page 3 — `/spend` Where the money went

- **Total: $0.50** across 241 requests
- **claude-sonnet-5: $0.29** — more than half your entire spend
- **claude-opus-5: $0.000165** — the single request from Step 4

Look at that first and third line together. **Sonnet ate 58% of your budget — and
`/compare` says you cannot prove it is any better than Haiku, which costs half as
much.** That is the exact business problem this project exists to solve, visible in
your own spending.

The bottom chart stacks spend by hour and by model.

---

## Page 4 — `/compare` The verdicts in full

The most important page. It opens by explaining itself:

> **Four verdicts, not three.** *Equivalent* means the difference was measured
> precisely and is small. *Inconclusive* means it was not measured precisely enough to
> say. Reporting both as "no significant difference" is how an underpowered run gets
> mistaken for a pass.

Each comparison shows a **horizontal bar** — this is the part to stare at.

```
        -10.00%        [==== grey band = ±3% ====]        +10.00%
                  |--------------------------------|
                        the range of possible truth
```

- The **grey band** is the margin: differences this small don't matter to you.
- The **coloured bar** is the range the true answer could be in.

For `haiku → sonnet` the bar runs from −10% to +10%. The grey band is only ±3%. **The
bar is more than three times wider than the band** — so it cannot possibly fit inside
it, no matter where the estimate lands. That is what "too wide to show equivalence"
means, and it is why the verdict is INCONCLUSIVE.

For `gpt-oss-20b → haiku` the bar runs +5.36% to +28.57%. It sits **entirely to the
right** of the grey band. Every value in that range is a meaningful improvement — so
the verdict is IMPROVEMENT, with no hedging.

Other fields:

- **Resolution ±10.0pp** — the smallest difference 60 questions can detect
- **Discordant 8** — questions where the two models disagreed. Only these carry
  information; the 52 they both got right or both got wrong tell you nothing
- **McNemar p 1.000000** — as strong an "identical" as a p-value ever gets, and the
  system *still* refused to conclude, because a p-value answers the wrong question

---

## Page 5 — `/traces` Every request, individually

A list of real requests. Click any one to expand it.

Find the red **`499 · client_abort · usage unconfirmed · $0`** row. That is the
request from Step 3 of the deeper walkthrough — the one killed mid-answer. The
dashboard shows all four facts: it was aborted, it cost nothing, the figure is
**unconfirmed**, and there is no response body.

Then find **"Say exactly: hello from verdict"** and click **Replay stream**, with
**1× / 2× / 4×** speed buttons. It replays the answer arriving word by word.

The caption under it is the detail worth noticing:

> *Reconstructed from a measured TTFT of 495ms and 546ms total — not a per-token recording.*

It tells you it is a **reconstruction**, not a recording. It would have been easy to
let people assume otherwise.

---

## What the whole site is really demonstrating

| The page shows | The habit it demonstrates |
| --- | --- |
| `/pareto` empty, with the command to fill it | Say "not measured", never fake it |
| Cache table with both rates side by side | Never show a benefit without its cost |
| `/compare` interval drawn to scale | Make "we don't know" *visible*, not a footnote |
| Replay labelled a reconstruction | Don't let people assume more than you measured |
| Footer on every page | A number not in `artifacts/` does not exist |

---

## That's it. You now understand the project.

The terminal showed you what it **does**:

1. A gateway that speaks OpenAI's language, so any OpenAI app works with it
2. Every request recorded, costed, and timed
3. A router that fails **expensive**, never cheap
4. Statistics honest enough to say *"I don't know"*

The dashboard showed you what it **found**:

1. Haiku is genuinely better than the cheap open model — **proven**
2. Sonnet costs twice Haiku's price and cannot be shown to be better — **unproven either way**
3. Sonnet is eating 58% of the budget on that unproven basis
4. The cache was built, measured, and rejected on the evidence

---

## What to do next

**If that all made sense**, the deeper version is [`WALKTHROUGH.md`](WALKTHROUGH.md)
— thirteen labs covering the parts this file skipped: what happens when you hang up
mid-answer, a real bug that refused 339 requests, and why the caching feature was
built, measured, and then deliberately thrown away.

**If any step confused you**, say which one. That is the useful thing to tell me —
more valuable than moving on.

**The one file to read before an interview:** [`DECISIONS.md`](DECISIONS.md).
Fifty-six decisions, each saying what you chose, what you rejected, and why.
