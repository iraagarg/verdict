# What to do next

Your personal checklist. Everything left, in order, with every command written out.

Work top to bottom. **Each task works on its own** — you can stop after any one of them and
nothing is broken.

---

# Where you are right now

**The project is finished.** All 8 phases built.

|                        |       |
| ---------------------- | ----- |
| Commits                | 28    |
| Tests passing          | 669   |
| Decisions written down | 61    |
| CI on GitHub           | green |
| Money spent so far     | $0.49 |

What is left is **not building**. It is: understanding it, putting it online, and recording a video.

---

# The six tasks

| #   | Task                      | Time   | Cost | Needed?             |
| --- | ------------------------- | ------ | ---- | ------------------- |
| 1   | See your own project run  | 20 min | free | **do this first**   |
| 2   | Database online (Neon)    | 10 min | free | for deploying       |
| 3   | Gateway online (Railway)  | 15 min | free | for deploying       |
| 4   | Dashboard online (Vercel) | 10 min | free | for deploying       |
| 5   | Automatic deploys         | 10 min | free | optional but nice   |
| 6   | Demo video                | 1 hour | free | good for interviews |

---

# Task 1 — See your own project run

**Why first:** you cannot talk about this in an interview until you have watched it work. Twenty
minutes here is worth more than anything else on this list.

## Start it

```bash
cd ~/Projects/flagship_project1
make up
```

**What this does:** starts five things — the database, Redis, a one-off job that creates your
database tables, your gateway, and your dashboard. It waits until every one reports healthy before
it finishes.

**What you should see at the end:**

```
  gateway   -> http://localhost:8080/health
  evald     -> http://localhost:8000/health
  dashboard -> http://localhost:3000

  next: open START-HERE.md
```

**If it fails:** run `docker compose logs` and read the last 20 lines. The failing service names
its own problem.

## Then follow the six steps

```bash
open START-HERE.md
```

That file has six commands. Copy one, paste it, read what comes back. Roughly ten minutes.

They show you, in order:

1. Your gateway is alive
2. It answers questions using OpenAI's exact format
3. Every request is recorded with its cost
4. When unsure, it picks the **expensive** model on purpose
5. The expensive one costs about 100× more
6. Your system says **INCONCLUSIVE** instead of guessing

**Step 6 is the whole project.** Read that one twice.

## Then look at the dashboard

```
http://localhost:3000
```

Five pages across the top. Part 2 of `START-HERE.md` explains each one.

**The page to spend time on is `/compare`.** It draws a grey band (the difference that would
matter) and a coloured bar (where the truth could be). For Haiku vs Sonnet the bar is three times
wider than the band — that is _why_ the answer is "we can't tell".

## When you finish

Tell me **which step or page confused you.** That is more useful than "done", because it tells me
where to explain more.

---

# Task 2 — Put your database online (Neon)

**Why this first among the deploy tasks:** your gateway refuses to start without a database
address. Set up Railway first and it will simply fail.

**What Neon is:** a company that runs a Postgres database on their computer, for free.

## Step 2.1 — Sign up

1. Go to **neon.tech**
2. Click **Sign up** → **Continue with GitHub**
3. It asks for a project name. Type `verdict`.
4. It asks for a region. Pick the one nearest you.
5. Click **Create project**

## Step 2.2 — Copy the connection string

After creating, Neon shows a **Connection string** box. It looks like:

```
postgresql://neondb_owner:AbC123xyz@ep-cool-name-12345-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
```

> ⚠️ **Neon offers two versions. You want the one with `pooler` in it.**
>
> Look for a dropdown or checkbox saying "Pooled connection" — turn it on.
>
> **Why:** your gateway opens 10 connections at once. The non-pooled address allows very few and
> will fail under any real use.

Copy the whole line.

## Step 2.3 — Create your tables

On your laptop:

```bash
cd ~/Projects/flagship_project1
export DATABASE_URL='paste-your-neon-string-here'
./tools/migrate.sh
```

> Keep the **single quotes** around the string. It contains characters your terminal would
> otherwise treat as commands.

**What this does:** creates the empty tables your project needs — like putting labelled folders in
a filing cabinet before you file anything.

**What you should see:**

```
applying 3 migration(s) to ep-cool-name-12345-pooler.ap-southeast-1.aws.neon.tech
  -> 0001_init.sql
  -> 0002_usage_finality.sql
  -> 0003_vectors.sql
migrations complete
```

Some `NOTICE: ... already exists, skipping` lines are normal and fine.

## Step 2.4 — Check it worked

```bash
psql "$DATABASE_URL" -c "\dt"
```

You should see a list including `traces`, `routes` and `semantic_cache_entries`.

**If `psql` is not installed:**

```bash
brew install libpq
brew link --force libpq
```

## Step 2.5 — Save the string somewhere

You need it again in Tasks 3 and 5. Put it in a note.

✅ **Task 2 done.** Your database is on the internet with the right tables. That is the hardest
part of deploying, and it is finished.

---

# Task 3 — Put your gateway online (Railway)

**What Railway is:** a company that runs your code 24/7 on their computer.

## Before you start — get two things ready

**1. Your Neon string** from Task 2.

**2. Your Groq key:**

```bash
cd ~/Projects/flagship_project1
grep GROQ_API_KEY .env
```

Copy everything after the `=`.

## Step 3.1 — Sign up

1. Go to **railway.app**
2. **Login** → **Login with GitHub**
3. Allow it to see your repositories

## Step 3.2 — Create the project

1. Click **New Project**
2. Choose **Deploy from GitHub repo**
3. Click **verdict**

Railway starts building straight away. **It will fail.** That is expected — you have not given it
the database address yet. Carry on.

## Step 3.3 — Rename the service to `gateway`

Railway shows a box on screen. That is your service, probably with a random name.

Click it → **Settings** → **Service Name** → change it to exactly:

```
gateway
```

**Why the exact name:** Task 5's automatic deploy looks for a service called `gateway`. A different
name means it will not find it.

## Step 3.4 — Add the four settings

Click your service → **Variables** tab → add these one at a time:

| Name                   | Value                   |
| ---------------------- | ----------------------- |
| `DATABASE_URL`         | your Neon pooled string |
| `COST_CAP_USD_PER_DAY` | `5.00`                  |
| `GROQ_API_KEY`         | your Groq key           |
| `NODE_ENV`             | `production`            |

**What each one is for:**

- **`DATABASE_URL`** — where to save every request and its cost.
- **`COST_CAP_USD_PER_DAY`** — a spending limit. Stops after $5 in a day, so a bug cannot run up a
  bill.
- **`GROQ_API_KEY`** — your permission to use Groq's AI models. At least one provider key is
  required.
- **`NODE_ENV`** — tells your code it is running for real, not on your laptop.

Railway redeploys automatically after you add them.

## Step 3.5 — Get your web address

**Settings** → **Networking** → **Generate Domain**

Railway gives you something like:

```
verdict-production-a1b2.up.railway.app
```

## Step 3.6 — Test it

```bash
curl -s https://verdict-production-a1b2.up.railway.app/health
```

Use your own address. You should see:

```json
{ "status": "ok", "service": "gateway", "git_sha": "...", "uptime_s": 12 }
```

## Step 3.7 — Send a real request through the internet

```bash
GATEWAY=https://verdict-production-a1b2.up.railway.app

curl -N $GATEWAY/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"openai/gpt-oss-20b","stream":true,
       "messages":[{"role":"user","content":"Say hello"}]}'
```

You should see the answer stream back word by word.

## Step 3.8 — Prove it was recorded

```bash
psql "$DATABASE_URL" -c \
  "SELECT model_served, input_tokens, output_tokens, cost_usd, usage_is_final
   FROM traces ORDER BY created_at DESC LIMIT 1;"
```

If a row appears with a real cost, **the entire path works**: request in → streamed out → tokens
counted → cost calculated → saved to a database on the internet.

✅ **Task 3 done.**

## When Railway goes wrong

Your gateway **checks every setting the moment it starts** and refuses to run if one is wrong. That
is deliberate — it fails immediately and names the problem, instead of breaking an hour later.

So when a deploy fails: click the service → **Deployments** → click the failed one → **read the
log**. It says exactly what is wrong. For example:

```
environment validation failed; refusing to start:
  - DATABASE_URL: must be a postgres:// or postgresql:// URL
```

| Problem                    | Cause              | Fix                              |
| -------------------------- | ------------------ | -------------------------------- |
| Cannot connect to database | wrong Neon string  | use the **pooler** one           |
| Starts, but requests fail  | key got truncated  | re-paste `GROQ_API_KEY` in full  |
| Keeps restarting           | a setting is wrong | read the log, fix that one       |
| Domain does not respond    | port mismatch      | add `GATEWAY_PORT` = `${{PORT}}` |

**On that last one:** Railway tells apps which port to use via a setting called `PORT`. Your
gateway looks for `GATEWAY_PORT`. Try without it first — it may be fine. If not, add that variable
typed exactly as shown, including the `${{ }}`.

## What Railway costs

$5 of free credit each month. You are charged for **time running**, not requests — roughly $3–5 a
month for one always-on service. If you exceed it, Railway pauses the service; nothing breaks, the
next request is just slow.

---

# Task 4 — Put your dashboard online (Vercel)

**What Vercel is:** a company that hosts websites. Best in the world at hosting Next.js, which is
what your dashboard is built with.

**This is the easiest task.** No settings, no passwords.

## Step 4.1 — Sign up and import

1. Go to **vercel.com**
2. **Sign up** → **Continue with GitHub**
3. Click **Add New** → **Project**
4. Find `verdict` → click **Import**

## Step 4.2 — The one setting that matters

Vercel shows a **Root Directory** box.

> ⚠️ **Leave it as the repo root. Do NOT set it to `apps/dashboard`.**
>
> **Why this matters:** your dashboard reads the `artifacts/` folder _while it builds_ and bakes
> those numbers into the finished pages. If Vercel only sees `apps/dashboard`, it cannot find that
> folder — and **every page will load perfectly but show no numbers at all.**
>
> This exact bug already happened to you in Docker. It is recorded as D-056. The build succeeded,
> the container reported healthy, and the dashboard had never displayed a single number.

## Step 4.3 — Deploy

Click **Deploy**. Wait about two minutes.

**No environment variables are needed.** Your dashboard has no database and no secrets — it only
reads files already in your repo. That was a deliberate design choice (D-052): it means the site
cannot break during a demo, because there is nothing live to break.

## Step 4.4 — Check every page

Vercel gives you an address like `verdict-xyz.vercel.app`. Open it and visit all five pages:

| Page       | What you should see                               |
| ---------- | ------------------------------------------------- |
| `/`        | two coloured verdict boxes                        |
| `/pareto`  | **empty at the top** (correct!) + the cache table |
| `/spend`   | $0.50 total, broken down by model                 |
| `/compare` | two verdicts with the bar drawn to scale          |
| `/traces`  | a list of real requests                           |

> **`/pareto` being empty is correct, not broken.** It says "No routing policy has been fitted" and
> tells you which command would produce the data. That is what an honest empty state looks like.

**If pages load but show no numbers:** you set the Root Directory wrong. Go to Settings → General →
Root Directory, clear it, and redeploy.

✅ **Task 4 done. Your project is live on the internet.**

---

# Task 5 — Make deploys automatic

**What this gives you:** right now, changing code means redeploying by hand. After this, pushing to
GitHub does everything automatically — run the tests, update the database, put the new version
online. And if the tests fail, **nothing deploys.**

## Step 5.1 — Get your Vercel IDs

```bash
cd ~/Projects/flagship_project1
npx vercel link
```

Answer the questions (link to the existing `verdict` project). Then:

```bash
cat .vercel/project.json
```

You will see two IDs — copy both:

```json
{ "orgId": "team_xxxxx", "projectId": "prj_xxxxx" }
```

## Step 5.2 — Get your tokens

- **Railway token:** railway.app → click your avatar → **Account Settings** → **Tokens** → **Create
  Token**
- **Vercel token:** vercel.com → **Settings** → **Tokens** → **Create**

## Step 5.3 — Add five secrets to GitHub

Go to:

```
https://github.com/iraagarg/verdict/settings/secrets/actions
```

Click **New repository secret** five times:

| Name                | Value                               |
| ------------------- | ----------------------------------- |
| `DATABASE_URL`      | your Neon pooled string             |
| `RAILWAY_TOKEN`     | from Railway                        |
| `VERCEL_TOKEN`      | from Vercel                         |
| `VERCEL_ORG_ID`     | the `orgId` from `project.json`     |
| `VERCEL_PROJECT_ID` | the `projectId` from `project.json` |

## Step 5.4 — Add one variable

Same page, **Variables** tab → **New repository variable**:

| Name          | Value                             |
| ------------- | --------------------------------- |
| `GATEWAY_URL` | `https://your-app.up.railway.app` |

**What this does:** after each deploy, GitHub checks your live gateway actually answers. Without
it, the deploy trusts Railway's word. A deploy that reports success while the service is broken is
not a success.

## Step 5.5 — Test it

```bash
cd ~/Projects/flagship_project1
git commit --allow-empty -m "test: trigger a deploy"
git push
```

Then watch: **github.com/iraagarg/verdict/actions**

You should see the Deploy workflow run four stages in order:

```
test  →  migrate  →  deploy gateway
                  →  deploy dashboard
```

## Why the order matters

**Migrations run BEFORE the new code goes live.** If it were the other way round, there would be a
window where new code runs against an old database — and things break in confusing ways.

✅ **Task 5 done.**

---

# Task 6 — Record the demo video

```bash
open DEMO.md
```

Ninety seconds, nine beats. Each beat says what to show, what to say, and how long.

## Before recording

```bash
make up
```

Then:

- Terminal font at **16–18pt** (smaller is unreadable once compressed)
- Browser at 1280×720, zoom 110%, **hide your bookmarks bar**
- Two tabs open: `/compare` and `/pareto`
- **Pre-type every command** and press Enter on the beat — do not type live

## The one beat that matters

**Beat 5, 18 seconds.** You show two models scoring identically, say every instinct says switch and
save half the money — then show your system saying **INCONCLUSIVE**.

Read that beat twice before you record. Do not rush it. Everything else is context.

## If you only get one take

Beats **4, 5 and 6** alone make a 40-second video that still works: it fails expensive, it refuses
to conclude, it commits when the evidence is there. That is the whole argument.

---

# Optional — two upgrades

**Neither is needed.** Your project is complete. These make it stronger.

## Upgrade A — Settle the Haiku vs Sonnet question (~$6)

Right now that comparison says INCONCLUSIVE because 60 questions is too few. About 667 would
settle it.

```bash
make bench CAP=6
```

**Possible outcome:** "Sonnet costs twice as much and is provably no better." That is a genuinely
strong result.

## Upgrade B — Calibrate the judge (4–6 hours of your time, free)

```bash
make label PAIRS=200
make calibrate PAIRS=200
```

Shows you 200 pairs of answers one at a time; you pick the better one. Then it measures how often
the AI judge agreed with you (Cohen's kappa).

**Do not do this unless you have a free afternoon.** It is genuinely tedious. But it is the only
way to claim your judge is validated.

---

# The one command to remember

Before **every** `git push`:

```bash
make check
```

Takes about 2 minutes. If it says **"All green. Safe to push"** — push.

If it complains about formatting:

```bash
make fmt
make check
```

**Why:** GitHub runs the same checks. Running them first means you never see a red ✗ on your repo.

---

# My advice on order

**Today:** Task 1 only. Twenty minutes watching your own project work.

**This week:** Task 2 (10 minutes). Just the database. It proves the hardest part works.

**Then:** Tasks 3 and 4 on separate days. No rush.

**Before applying for jobs:** Task 6, the video.

Do not try to do all of it in one sitting. Each task stands alone, and **your project is already
finished** — everything here makes it more visible, not more complete.
