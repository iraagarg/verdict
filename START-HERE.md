# Start here — your project in six steps

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

## That's it. You now understand the project.

Six commands showed you:

1. A gateway that speaks OpenAI's language
2. Every request recorded, costed, and timed
3. A router that fails **expensive**, never cheap
4. Statistics honest enough to say *"I don't know"*

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
