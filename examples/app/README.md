# Ask Anything

A small app that uses Verdict. Not a script — a server, a page, and a streaming answer.

```bash
node examples/app/server.mjs
open http://localhost:4000
```

Type a question, pick a model, watch the answer stream in. Above it the app shows **which model
answered, through which provider, and why** — the three things a plain OpenAI call cannot tell you,
and the reason to put a gateway in front at all.

---

## The browser never calls the gateway

It would be one line to fetch the gateway from the page, and one more on the gateway to allow the
origin. Both would be wrong.

**The gateway holds the provider API keys.** A browser that can reach it is a browser anyone can
point at your account and spend from. That is why this gateway ships no CORS headers — a refusal,
not an oversight.

So this app does what every real one does:

```
browser  →  this app's server  →  Verdict  →  Groq / Anthropic
```

The trust boundary is the app server. That is also where a real product would put per-user auth and
rate limits, neither of which belongs in a shared gateway.

## The dropdown is not hard-coded

The page asks the gateway what it can serve (`GET /v1/models`) and builds the list from the answer.

The first version hard-coded three models. Two of them errored, because the deployed gateway holds
only a Groq key — and no dropdown can know that. The same config deployed with different keys serves
a different set, so a client has to ask rather than assume (**D-073**).

| Running against                         | You see                                    |
| --------------------------------------- | ------------------------------------------ |
| the deployed gateway                    | the two Groq models, and no `verdict-auto` |
| a local `make up` with an Anthropic key | five models, plus `verdict-auto`           |

`verdict-auto` appears only when `safe_default` is itself servable. Advertising a routing alias that
resolves to a model this deployment cannot call would be advertising a guaranteed failure.

## Try the model selector

| Choose             | What happens                                          |
| ------------------ | ----------------------------------------------------- |
| a named model      | `why: explicit_model` — you asked, the gateway obeyed |
| **`verdict-auto`** | **the gateway chooses**, and says why                 |

Against the deployed gateway `verdict-auto` is not offered at all. Request it by hand and you get:

> No API key is configured for the provider that serves `claude-opus-5`.

**That is the system working.** With no fitted policy, the router falls back to `safe_default` —
the strongest, most expensive rung — because ambiguity resolves toward quality. The deployed gateway
has only a Groq key, so it **refuses the request** rather than quietly serving something cheaper.

Failing loudly beats downgrading silently. A cheap answer nobody asked for is the failure this whole
project exists to prevent.

To see `verdict-auto` succeed, run it against a local gateway with an Anthropic key in `.env`:

```bash
make up
VERDICT_URL=http://localhost:8080 node examples/app/server.mjs
```

## Close the tab mid-answer

The app aborts its call to the gateway when the browser disconnects, and the gateway aborts its call
to the provider. Nobody pays for tokens nobody will read.

That only works because every layer propagates the signal instead of swallowing it — the gateway
side is **D-020**, and `res.on("close")` in `server.mjs` is the app side.
