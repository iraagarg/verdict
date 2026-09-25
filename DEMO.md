# 90-second demo video script

Nine beats. Each one is a screen, a spoken line, and the exact thing to do.

**The spine:** _the cheapest model is 67× cheaper, nobody can prove it's safe, here is a system that
refuses to guess._ Every beat serves that. Nothing here is a feature tour.

**The moment the video exists for is Beat 5** — the system saying _no_ when every instinct says yes.
Everything before it is setup; everything after is consequence. If you cut anything, cut Beat 8.

---

## Before you record

```bash
cd ~/Projects/flagship_project1
make up                    # waits until everything is healthy
make loadtest              # ~1 min; only if you want fresh numbers on screen
```

Then:

- Terminal at **16–18pt**. Anything smaller is unreadable when compressed.
- Browser at **1280×720**, zoom 110%, **no bookmarks bar**, no extension icons.
- Two tabs ready: `localhost:3000/compare` and `localhost:3000/pareto`.
- Dark mode both, so cuts don't flash.
- Pre-type each command and press Enter on the beat. Do not type live.

---

## Beat 1 · 0:00–0:08 · The problem

**Screen:** `config/models.yaml`, scrolled so `gpt-oss-20b` and `claude-opus-5` pricing are both
visible. Highlight the two `input:` lines.

> "Same question, two models. One costs sixty-seven times more per word in, eighty-three times more
> per word out. The only question that matters is: can I use the cheap one?"

---

## Beat 2 · 0:08–0:18 · It's a real gateway

**Screen:** terminal, full width.

```bash
curl -N localhost:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"openai/gpt-oss-20b","stream":true,
       "messages":[{"role":"user","content":"What is the capital of France?"}]}'
```

Let the SSE frames stream. Don't cut early — the streaming is the point.

> "Verdict is an OpenAI-compatible gateway. Same API, same wire format, so any existing app points
> at it and works unchanged. It just watches everything that goes through."

---

## Beat 3 · 0:18–0:28 · Everything is measured

**Screen:** same terminal.

```bash
docker compose exec -T postgres psql -U verdict -d verdict -x -c \
  "SELECT model_served, input_tokens, output_tokens, cost_usd, usage_is_final
   FROM traces ORDER BY created_at DESC LIMIT 1;"
```

Hold on `usage_is_final`.

> "Every request is recorded — model, tokens, exact cost in integer nano-USD, no floating point.
> And `usage_is_final`: if a stream is cut, the cost is flagged unconfirmed rather than reported as
> zero. It never guesses a number it doesn't have."

---

## Beat 4 · 0:28–0:38 · It fails expensive

**Screen:** terminal.

```bash
curl -s -D - -o /dev/null localhost:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"verdict-auto","max_tokens":5,
       "messages":[{"role":"user","content":"hi"}]}' | grep x-verdict
```

Highlight `model-served: claude-opus-5` and `route-reason: router_off`.

> "Ask it to choose and it picks the most expensive model in the ladder. Look at the reason: no
> policy is loaded, so it has no proof a cheaper one is safe. Six of the seven paths through the
> router return the expensive model. It fails expensive — a mistake you see on a bill, not one you
> hear about from angry users."

---

## Beat 5 · 0:38–0:56 · The moment 🎯

**Screen:** cut to browser, `localhost:3000/compare`. Scroll so the Haiku→Sonnet card fills the
frame. Hold on the interval bar.

Slow down. Let the bar sit on screen for a full two seconds before speaking.

> "Here are two models scoring identically. Eighty-one point seven percent each. Sonnet costs twice
> as much. Every instinct says switch and halve the bill."
>
> _(beat)_
>
> "Verdict says no. The grey band is the difference that would matter. The coloured bar is where the
> truth could be — and it's three times wider than the band. Sixty questions only resolve to plus or
> minus ten points, so a real eight-point gap would look exactly like this."
>
> "So the answer isn't 'they're the same'. It's **inconclusive**. Go and get more data."

**Do not rush this beat.** It is the thesis. Everything else is context.

---

## Beat 6 · 0:56–1:06 · It isn't just cautious

**Screen:** same page, scroll down to the `gpt-oss-20b → claude-haiku-4-5` card. Green badge visible.

> "And when there is a real difference, it commits immediately. Sixteen points, p equals nought
> point nought two two. Improvement, no hedging."
>
> "That's the distinction most eval tools collapse. 'No significant difference' can mean _we
> measured carefully and they match_ or _we didn't measure enough to tell_. Those are opposite
> conclusions, and only one of them lets you spend less."

---

## Beat 7 · 1:06–1:18 · It killed its own feature

**Screen:** `localhost:3000/pareto`. The cache table, `Safe?` column in frame.

> "I built a semantic cache — reuse an old answer when someone asks the same thing differently.
> Then I measured it. Loose settings save half your calls and get fourteen percent of answers
> wrong. Tight settings are safe and never fire."
>
> "The only 'safe' row has a zero percent hit rate. So the gateway ships no cache, and the dashboard
> says so."

If you have three spare seconds, show the killer pair: _4 distinguishable balls into 2
indistinguishable boxes_ versus _4 indistinguishable balls into 2 distinguishable boxes_ — two words
swapped, different answers, 99.86% similar.

---

## Beat 8 · 1:18–1:26 · It's fast enough not to matter

**Screen:** terminal, `cat artifacts/loadtest.json | head -40`, or the README performance table.

> "The gateway adds under a millisecond at p50 and six at p99, sustaining a hundred and fifty-eight
> thousand requests a minute with zero errors. A real provider call takes eight hundred. The
> measurement layer is a tenth of a percent of the request."

**This is the beat to cut if you're over time.**

---

## Beat 9 · 1:26–1:30 · The close

**Screen:** `README.md` scrolled to the _Status_ section, on **"Not claimed: any cost saving."**

> "I built a cost-optimisation tool and I'm not claiming a cost saving — because I haven't run the
> replay that would prove one. Every number here comes from a committed artifact, and CI fails if
> the README disagrees with one."

Hold two seconds. End.

---

## Timing

| Beat                   | Length  | Cut priority  |
| ---------------------- | ------- | ------------- |
| 1 · problem            | 8s      | keep          |
| 2 · real gateway       | 10s     | trim to 7s    |
| 3 · measured           | 10s     | trim to 7s    |
| 4 · fails expensive    | 10s     | keep          |
| **5 · the moment**     | **18s** | **never cut** |
| 6 · commits when real  | 10s     | keep          |
| 7 · killed its feature | 12s     | trim to 8s    |
| 8 · performance        | 8s      | **cut first** |
| 9 · close              | 4s      | keep          |

Total 90s. Cutting Beat 8 and trimming 2, 3 and 7 gets you to 68s.

---

## What not to do

- **Don't narrate the architecture.** Nobody watching a 90-second video needs to know it's Fastify
  and pnpm workspaces. The code is one click away if they care.
- **Don't apologise for what isn't finished.** Beat 9 states it flatly and moves on. Flat beats
  sheepish.
- **Don't say "as you can see".** Show it or don't.
- **Don't speed up the terminal.** A recording sped past readability reads as hiding something.
- **Don't add music.** It competes with the voice and dates the video.

---

## If you only get one take

Beats 4, 5 and 6, in that order, is a **40-second video that still lands**: it fails expensive, it
refuses to conclude, and it commits when the evidence is there. That is the whole argument.
