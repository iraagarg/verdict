/**
 * Fastify application factory.
 *
 * P0 scaffold: health and readiness only. The OpenAI-compatible surface
 * (POST /v1/chat/completions) lands in P1 — see DESIGN.md §6.
 */
import Fastify, { LogController } from "fastify";
import { randomUUID } from "node:crypto";
import { createLogger } from "./logger.js";
import { registerChatRoutes } from "./routes/chat.js";
import type { GatewayApp } from "./http.js";
import type { GatewayServices } from "./services.js";
import type { Env } from "./env.js";

export type { GatewayApp } from "./http.js";

export interface BuildOptions {
  env: Env;
  services: GatewayServices;
}

/** Reported by /health so a running container can be tied back to a commit. */
const GIT_SHA = process.env["GIT_SHA"] ?? "unknown";

export function buildApp({ env, services }: BuildOptions): GatewayApp {
  const app = Fastify({
    loggerInstance: createLogger(env),
    // Trust an inbound request id when a caller supplies one, so a trace can
    // span the dashboard, the gateway and evald. Mint one otherwise.
    genReqId: (req) => {
      const header = req.headers["x-request-id"];
      if (typeof header === "string" && header.length > 0 && header.length <= 200) return header;
      return randomUUID();
    },
    requestIdHeader: false,
    // Fastify 5 moved these off the top level; the top-level options are
    // deprecated and go away in Fastify 6.
    logController: new LogController({
      requestIdLogLabel: "request_id",
      disableRequestLogging: false,
    }),
    bodyLimit: 4 * 1024 * 1024,
  });

  // The request id is echoed on every response, success or failure.
  app.addHook("onRequest", async (req, reply) => {
    void reply.header("x-verdict-request-id", req.id);
  });

  /**
   * Index.
   *
   * This is an API, so a 404 on `/` was not wrong — but the URL gets opened in
   * a browser by people deciding whether the thing works, and an error page is
   * a poor answer to "is this real?". Content-negotiated rather than picking
   * one: a browser sends `Accept: text/html` and gets something readable, any
   * API client gets the machine-readable form, and neither is compromised for
   * the other (D-069).
   */
  app.get("/", async (req, reply) => {
    const index = {
      service: "verdict-gateway",
      description:
        "OpenAI-compatible LLM gateway. Routes traffic to the cheapest model that provably clears a statistical quality floor.",
      git_sha: GIT_SHA,
      uptime_s: Math.floor(process.uptime()),
      endpoints: {
        "POST /v1/chat/completions": "OpenAI-compatible, streaming and non-streaming",
        "GET /health": "liveness — does not touch the datastore",
        "GET /ready": "readiness — dependency and circuit-breaker state",
      },
      dashboard: "https://verdict-navy.vercel.app",
      source: "https://github.com/iraagarg/verdict",
    };

    const accept = req.headers.accept ?? "";
    if (!accept.includes("text/html")) return index;

    // Deliberately one self-contained page with no external requests: an index
    // that needs a CDN to render can fail in ways the service it describes has
    // not.
    return reply.type("text/html; charset=utf-8").send(`<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verdict gateway</title>
<style>
  :root { color-scheme: dark light; }
  body { margin:0; padding:2.5rem 1.25rem; background:#0d1117; color:#e6edf3;
         font:16px/1.65 ui-sans-serif,system-ui,-apple-system,sans-serif; }
  main { max-width:44rem; margin:0 auto; }
  h1 { font-size:1.5rem; margin:0 0 .4rem; letter-spacing:-.01em; }
  p { color:#9daab8; max-width:38rem; }
  code,pre { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.85rem; }
  pre { background:#161b22; border:1px solid #30363d; border-radius:6px;
        padding:.9rem 1rem; overflow-x:auto; color:#e6edf3; }
  table { border-collapse:collapse; width:100%; margin:1.25rem 0; font-size:.9rem; }
  td { padding:.45rem .8rem .45rem 0; border-bottom:1px solid #21262d; vertical-align:top; }
  td:first-child { white-space:nowrap; color:#7ee787; }
  a { color:#6cb6ff; }
  .ok { display:inline-block; padding:.15rem .5rem; border-radius:999px; font-size:.75rem;
        background:#12261a; color:#7ee787; border:1px solid #1f4429; }
  footer { margin-top:2rem; padding-top:1rem; border-top:1px solid #21262d;
           color:#6e7d8f; font-size:.85rem; }
</style></head><body><main>
<h1>Verdict gateway <span class="ok">running</span></h1>
<p>An OpenAI-compatible LLM gateway. It routes traffic to the cheapest model that provably clears a
statistical quality floor &mdash; and refuses to route cheap when it cannot prove that.</p>
<p>This host is the API. There is no UI here; the measurements are on the
<a href="https://verdict-navy.vercel.app">dashboard</a>.</p>
<table>
  <tr><td>POST /v1/chat/completions</td><td>OpenAI-compatible, streaming and non-streaming</td></tr>
  <tr><td>GET /health</td><td>liveness &mdash; does not touch the datastore</td></tr>
  <tr><td>GET /ready</td><td>readiness &mdash; dependencies and circuit-breaker state</td></tr>
</table>
<p>Change one line in any OpenAI client &mdash; the base URL &mdash; and it works unchanged:</p>
<pre>curl -N https://verdictgateway-production.up.railway.app/v1/chat/completions \\
  -H 'content-type: application/json' \\
  -d '{"model":"openai/gpt-oss-20b","stream":true,
       "messages":[{"role":"user","content":"Say hello"}]}'</pre>
<footer>
  <a href="https://verdict-navy.vercel.app">Dashboard</a> &middot;
  <a href="https://github.com/iraagarg/verdict">Source</a> &middot;
  build ${GIT_SHA} &middot; up ${String(Math.floor(process.uptime()))}s
</footer>
</main></body></html>`);
  });

  /**
   * Liveness. Answers "is this process running?" and must not touch a
   * datastore — a Postgres outage does not mean the gateway should be killed
   * and restarted. DESIGN.md failure mode #7.
   */
  app.get("/health", async () => ({
    status: "ok",
    service: "gateway",
    git_sha: GIT_SHA,
    uptime_s: Math.floor(process.uptime()),
  }));

  /**
   * Readiness. Answers "should traffic be sent here?".
   *
   * P0 returns the dependency checks as an empty set. When P1 adds Postgres and
   * Redis, degraded dependencies belong here — but per DESIGN.md failure modes
   * #7 and #8 a degraded datastore must NOT make the gateway unready, because
   * Verdict serves traffic without its analytics store. Only a condition that
   * genuinely prevents serving may return 503.
   */
  app.get("/ready", async () => {
    // Per DESIGN.md failure modes #7 and #8, a degraded datastore must NOT make
    // the gateway unready: Verdict serves traffic without its analytics store.
    // These are reported for visibility, never to gate traffic.
    const breakers: Record<string, string> = {};
    for (const [name, breaker] of services.registry.breakers()) breakers[name] = breaker.state;
    return {
      status: "ready",
      dependencies: {
        traces_queued: services.traces.size,
        traces_dropped: services.traces.dropped,
        trace_flush_failures: services.traces.flushFailures,
        breakers,
      },
    };
  });

  registerChatRoutes(app, services);

  app.setNotFoundHandler(async (req, reply) => {
    // OpenAI-shaped error body, so a client's error parser keeps working.
    return reply.code(404).send({
      error: {
        message: `Unknown route: ${req.method} ${req.url}`,
        type: "invalid_request_error",
        param: null,
        code: "not_found",
      },
    });
  });

  return app;
}
