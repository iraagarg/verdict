/**
 * "Ask Anything" — a small app that uses Verdict.
 *
 * ## Why the browser never talks to the gateway
 *
 * It would be one line to fetch the gateway from the page and one more to allow
 * the origin. Both would be wrong. The gateway holds the provider API keys, so a
 * browser that can reach it is a browser anyone can point at your account and
 * spend your money from. That is why this gateway ships no CORS headers: not an
 * oversight, a refusal.
 *
 * So this app does what every real one does — its own server calls the gateway,
 * and the browser only ever talks to this server. The trust boundary is here.
 *
 * ## What it demonstrates
 *
 * The routing headers. A plain OpenAI call cannot tell you which model answered
 * or why; that information is the reason to put a gateway in front at all. The
 * page shows it on every answer, and the model selector lets you watch
 * `verdict-auto` fall back to the expensive rung when no policy is loaded.
 *
 *   node examples/app/server.mjs      →  http://localhost:4000
 */
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const GATEWAY = process.env.VERDICT_URL ?? "https://verdictgateway-production.up.railway.app";
const PORT = Number(process.env.PORT ?? 4000);

const page = readFileSync(join(HERE, "index.html"), "utf8");

/** Read a JSON body, refusing anything implausibly large. */
async function readJson(req, limitBytes = 64 * 1024) {
  const chunks = [];
  let total = 0;
  for await (const c of req) {
    total += c.length;
    if (total > limitBytes) throw new Error("request body too large");
    chunks.push(c);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

const server = createServer(async (req, res) => {
  if (req.method === "GET" && (req.url === "/" || req.url === "/index.html")) {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(page.replace("__GATEWAY__", GATEWAY));
    return;
  }

  if (req.method !== "POST" || req.url !== "/api/ask") {
    res.writeHead(404, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "not found" }));
    return;
  }

  let body;
  try {
    body = await readJson(req);
  } catch (err) {
    res.writeHead(400, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: String(err.message ?? err) }));
    return;
  }

  const question = String(body.question ?? "").slice(0, 2000);
  const model = String(body.model ?? "openai/gpt-oss-20b");
  if (question.trim() === "") {
    res.writeHead(400, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "question is required" }));
    return;
  }

  // If the visitor navigates away, stop paying for tokens nobody will read.
  // The gateway aborts its upstream call when this request is aborted, which is
  // the whole point of the disconnect handling in D-020 — it only works if the
  // app in front propagates the signal instead of swallowing it.
  const upstream = new AbortController();
  res.on("close", () => {
    if (!res.writableEnded) upstream.abort();
  });

  let gw;
  try {
    gw = await fetch(`${GATEWAY}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model,
        stream: true,
        messages: [{ role: "user", content: question }],
      }),
      signal: upstream.signal,
    });
  } catch (err) {
    if (upstream.signal.aborted) return;
    res.writeHead(502, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: `gateway unreachable: ${String(err.message ?? err)}` }));
    return;
  }

  if (!gw.ok || !gw.body) {
    const text = await gw.text().catch(() => "");
    res.writeHead(gw.status, { "content-type": "application/json" });
    res.end(text || JSON.stringify({ error: `gateway returned ${gw.status}` }));
    return;
  }

  // Hand the routing decision to the page BEFORE the answer, as its own SSE
  // event. It is available immediately and the answer is not, so waiting would
  // leave the page unable to say what it is waiting for.
  res.writeHead(200, {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache",
    connection: "keep-alive",
  });
  res.write(
    `event: route\ndata: ${JSON.stringify({
      model: gw.headers.get("x-verdict-model-served"),
      provider: gw.headers.get("x-verdict-provider"),
      reason: gw.headers.get("x-verdict-route-reason"),
      requestId: gw.headers.get("x-verdict-request-id"),
    })}\n\n`,
  );

  try {
    for await (const chunk of gw.body) res.write(chunk);
  } catch {
    // Aborted mid-stream. The close handler already told the gateway.
  }
  res.end();
});

server.listen(PORT, () => {
  process.stdout.write(
    `\n  Ask Anything  →  http://localhost:${PORT}\n` + `  using gateway →  ${GATEWAY}\n\n`,
  );
});
