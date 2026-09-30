/**
 * A real app using Verdict, written the way any app would be.
 *
 * The point of this file is the line marked ONE LINE below. Everything else is
 * the official OpenAI SDK, used exactly as its own documentation shows. Nothing
 * here knows Verdict exists: no custom client, no wrapper, no special headers.
 * That is what "OpenAI-compatible" has to mean to be worth claiming — not
 * "similar", but "the same SDK, unmodified, pointed somewhere else".
 *
 *   node examples/chat.mjs "your question here"
 */
import OpenAI from "openai";

const GATEWAY = process.env.VERDICT_URL ?? "https://verdictgateway-production.up.railway.app";

const client = new OpenAI({
  baseURL: `${GATEWAY}/v1`, // <-- ONE LINE. Without it this calls OpenAI directly.
  apiKey: "not-used-by-verdict", // the SDK requires a value; the gateway holds the real keys
});

const question =
  process.argv.slice(2).join(" ") || "Explain what an API gateway is, in two sentences.";

// `.withResponse()` hands back the raw HTTP response alongside the stream, which
// is how an app reads what Verdict decided. A plain OpenAI call has nothing to
// report here; these headers are the whole reason to put a gateway in front.
const { data: stream, response } = await client.chat.completions
  .create({
    model: "openai/gpt-oss-20b",
    stream: true,
    messages: [{ role: "user", content: question }],
  })
  .withResponse();

process.stdout.write(`\n  ${question}\n\n  `);
for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");
}

process.stdout.write(
  `\n\n  ─────────────────────────────────────────────\n` +
    `  model    ${response.headers.get("x-verdict-model-served")}\n` +
    `  provider ${response.headers.get("x-verdict-provider")}\n` +
    `  why      ${response.headers.get("x-verdict-route-reason")}\n`,
);

// The cost is deliberately absent above. HTTP headers are sent BEFORE the body,
// and for a streamed answer nobody knows the token count until the last token
// has been written -- so no honest gateway can put a final cost in a streaming
// response header. It arrives on a non-streaming call, where the answer is
// complete before the response is sent:
const { data: full, response: res2 } = await client.chat.completions
  .create({
    model: "openai/gpt-oss-20b",
    messages: [{ role: "user", content: "Reply with one word: ok" }],
  })
  .withResponse();

process.stdout.write(
  `\n  same request, not streamed:\n` +
    `  answer   ${full.choices[0]?.message?.content?.trim()}\n` +
    `  tokens   ${full.usage?.prompt_tokens} in, ${full.usage?.completion_tokens} out\n` +
    `  cost     $${res2.headers.get("x-verdict-cost-usd")}\n` +
    `  final?   ${res2.headers.get("x-verdict-usage-final")}\n\n`,
);
