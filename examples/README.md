# examples/

A consumer app. It depends on the **official OpenAI SDK** and on nothing in this repo.

That is the point. "OpenAI-compatible" is only worth claiming if the ordinary SDK works unmodified —
not a fork, not a wrapper, not a special client. This directory is where that claim gets checked
instead of asserted, which is why it sits outside `apps/` and takes no dependency on
`@verdict/shared`.

```bash
pnpm install
node examples/chat.mjs "What is an API gateway? One sentence."
```

## The only line that mentions Verdict

```js
const client = new OpenAI({
  baseURL: "https://verdictgateway-production.up.railway.app/v1", // <- this
  apiKey: "not-used-by-verdict",
});
```

Delete that `baseURL` and the same file calls OpenAI directly. Nothing else changes — same
`client.chat.completions.create`, same streaming loop, same response shape.

`apiKey` is required by the SDK and ignored by the gateway, which holds the real provider keys
server-side. An app using Verdict never sees them.

## What it prints

```
  model    openai/gpt-oss-20b
  provider groq
  why      explicit_model

  same request, not streamed:
  answer   ok
  tokens   77 in, 57 out
  cost     $0.00002288
  final?   true
```

The first three lines are what a plain OpenAI call cannot tell you — which model answered, through
which provider, and **why**. `why` is the routing decision: `explicit_model` here because the app
named a model. Send `"model": "verdict-auto"` and the gateway chooses, reporting its reason.

## Why cost is missing from the streaming call

HTTP headers are sent **before** the body. For a streamed answer nobody knows the token count until
the last token is written, so no honest gateway can put a final cost in a streaming response header.

It appears on the non-streaming call, where the answer is complete before the response is sent — and
`final? true` says the provider confirmed those numbers rather than them being estimated. For
streamed requests the cost lands on the trace row in Postgres once the stream closes.

## Point it at your own gateway

```bash
VERDICT_URL=http://localhost:8080 node examples/chat.mjs "hello"
```
