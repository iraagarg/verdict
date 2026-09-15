/**
 * Integration tests for POST /v1/chat/completions.
 *
 * These run the real Fastify app, the real Anthropic SDK and a real HTTP
 * server speaking Anthropic's SSE protocol. The gateway is listening on a real
 * socket for the streaming cases, because `inject()` cannot reproduce a client
 * that hangs up mid-stream — which is one of the behaviours under test.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { buildApp } from "../app.js";
import { buildServices } from "../services.js";
import { loadEnv, type Env } from "../env.js";
import { anthropicScript, startMockProvider, type MockProvider } from "../testing/mock-provider.js";
import type { TraceRow } from "../trace/repository.js";
import type { GatewayApp } from "../http.js";

let provider: MockProvider;
let app: GatewayApp;
let traces: TraceRow[];
let services: ReturnType<typeof buildServices>;
let baseUrl: string;

function envWith(overrides: Record<string, string>): Env {
  return loadEnv({
    DATABASE_URL: "postgres://verdict:verdict@localhost:5432/verdict",
    REDIS_URL: "redis://localhost:6379",
    ANTHROPIC_API_KEY: "sk-ant-test-key",
    COST_CAP_USD_PER_DAY: "5.00",
    LOG_LEVEL: "fatal",
    MODELS_CONFIG_PATH: "../../config/models.yaml",
    ANTHROPIC_BASE_URL: provider.url,
    MAX_RETRIES: "0",
    TRACE_BATCH_SIZE: "1",
    ...overrides,
  });
}

async function boot(overrides: Record<string, string> = {}): Promise<void> {
  traces = [];
  const env = envWith(overrides);
  services = buildServices({
    env,
    traceSink: async (rows) => {
      traces.push(...rows);
    },
  });
  app = buildApp({ env, services });
  await app.listen({ port: 0, host: "127.0.0.1" });
  const addr = app.server.address();
  baseUrl = typeof addr === "object" && addr !== null ? `http://127.0.0.1:${addr.port}` : "";
}

/** Wait for the write-behind queue to hand a trace to the sink. */
async function traceFor(): Promise<TraceRow> {
  for (let i = 0; i < 100; i++) {
    await services.traces.flush();
    if (traces.length > 0) return traces[0]!;
    await new Promise((r) => setTimeout(r, 10));
  }
  throw new Error("no trace was recorded");
}

async function readSse(res: Response): Promise<string[]> {
  const text = await res.text();
  return text
    .split("\n\n")
    .filter((f) => f.startsWith("data: "))
    .map((f) => f.slice(6));
}

beforeEach(async () => {
  provider = await startMockProvider();
});

afterEach(async () => {
  await app?.close();
  await services?.close();
  await provider?.close();
});

describe("POST /v1/chat/completions — validation", () => {
  beforeEach(async () => {
    provider.setScript({ events: anthropicScript(["hi"]) });
    await boot();
  });

  it("rejects an unknown field with an OpenAI-shaped 400", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/v1/chat/completions",
      payload: { model: "claude-haiku-4-5", messages: [{ role: "user", content: "x" }], tools: [] },
    });
    expect(res.statusCode).toBe(400);
    expect(res.json()).toMatchObject({ error: { type: "invalid_request_error" } });
  });

  it("404s an unknown model without calling any provider", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/v1/chat/completions",
      payload: { model: "gpt-imaginary", messages: [{ role: "user", content: "x" }] },
    });
    expect(res.statusCode).toBe(404);
    expect(res.json()).toMatchObject({ error: { code: "model_not_found" } });
    expect(provider.requests).toHaveLength(0);
  });

  it("503s a model whose provider has no configured key", async () => {
    // No OPENAI_API_KEY in this environment, so gpt-5 is configured but unservable.
    const res = await app.inject({
      method: "POST",
      url: "/v1/chat/completions",
      payload: { model: "gpt-5", messages: [{ role: "user", content: "x" }] },
    });
    expect(res.statusCode).toBe(503);
    expect(res.json()).toMatchObject({ error: { code: "provider_unconfigured" } });
  });

  it("rejects temperature on a pinned model that does not accept it", async () => {
    // claude-opus-5 returns HTTP 400 for temperature. The caller chose the
    // model, so the incompatibility is theirs and a silent drop would mislead.
    const res = await app.inject({
      method: "POST",
      url: "/v1/chat/completions",
      payload: {
        model: "claude-opus-5",
        messages: [{ role: "user", content: "x" }],
        temperature: 0.7,
      },
    });
    expect(res.statusCode).toBe(400);
    expect(res.json()).toMatchObject({
      error: { code: "unsupported_parameter", param: "temperature" },
    });
    expect(provider.requests).toHaveLength(0);
  });

  it("drops temperature and reports it when Verdict picked the model", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/v1/chat/completions",
      payload: {
        model: "verdict-auto",
        messages: [{ role: "user", content: "x" }],
        temperature: 0.7,
      },
    });
    expect(res.statusCode).toBe(200);
    expect(res.headers["x-verdict-dropped-params"]).toBe("temperature");
    const sent = provider.requests[0]?.body as Record<string, unknown>;
    expect(sent.temperature).toBeUndefined();
  });
});

describe("POST /v1/chat/completions — non-streaming", () => {
  beforeEach(async () => {
    provider.setScript({ events: anthropicScript(["Hello", " world"], { input: 12, output: 7 }) });
    await boot();
  });

  it("returns an OpenAI chat.completion", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/v1/chat/completions",
      payload: { model: "claude-haiku-4-5", messages: [{ role: "user", content: "hi" }] },
    });
    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.object).toBe("chat.completion");
    expect(body.choices[0].message.content).toBe("Hello world");
    expect(body.choices[0].finish_reason).toBe("stop");
    expect(body.usage).toEqual({ prompt_tokens: 12, completion_tokens: 7, total_tokens: 19 });
  });

  it("computes cost from provider-reported tokens", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/v1/chat/completions",
      payload: { model: "claude-haiku-4-5", messages: [{ role: "user", content: "hi" }] },
    });
    // haiku: $1/MTok in, $5/MTok out. 12 * 1000 + 7 * 5000 = 47_000 nano = $0.000047
    expect(res.headers["x-verdict-cost-usd"]).toBe("0.00004700");
    expect(res.headers["x-verdict-usage-final"]).toBe("true");
  });

  it("maps verdict-auto to the configured safe_default", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/v1/chat/completions",
      payload: { model: "verdict-auto", messages: [{ role: "user", content: "hi" }] },
    });
    expect(res.headers["x-verdict-model-served"]).toBe("claude-opus-5");
  });

  it("forwards system messages as Anthropic's top-level system field", async () => {
    await app.inject({
      method: "POST",
      url: "/v1/chat/completions",
      payload: {
        model: "claude-haiku-4-5",
        messages: [
          { role: "system", content: "be terse" },
          { role: "user", content: "hi" },
        ],
      },
    });
    const sent = provider.requests[0]?.body as Record<string, unknown>;
    expect(sent.system).toBe("be terse");
    expect(sent.messages).toEqual([{ role: "user", content: "hi" }]);
  });
});

describe("POST /v1/chat/completions — streaming", () => {
  beforeEach(async () => {
    provider.setScript({
      events: anthropicScript(["Hel", "lo", "!"], { input: 20, output: 9 }),
      delayMs: 1,
    });
    await boot();
  });

  it("emits OpenAI chunks terminated by [DONE]", async () => {
    const res = await fetch(`${baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: "claude-haiku-4-5",
        messages: [{ role: "user", content: "hi" }],
        stream: true,
      }),
    });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/event-stream");

    const frames = await readSse(res);
    expect(frames.at(-1)).toBe("[DONE]");

    const chunks = frames.slice(0, -1).map((f) => JSON.parse(f));
    expect(chunks[0].choices[0].delta.role).toBe("assistant");
    const text = chunks.map((c) => c.choices[0]?.delta?.content ?? "").join("");
    expect(text).toBe("Hello!");
    expect(chunks.at(-1).choices[0].finish_reason).toBe("stop");
  });

  it("streams rather than buffering: chunks arrive before the upstream finishes", async () => {
    // If the gateway buffered, nothing would be readable until the whole
    // upstream response had completed.
    provider.setScript({ events: anthropicScript(["a", "b", "c", "d"]), delayMs: 40 });
    const res = await fetch(`${baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: "claude-haiku-4-5",
        messages: [{ role: "user", content: "hi" }],
        stream: true,
      }),
    });
    const reader = res.body!.getReader();
    const firstChunkAt = Date.now();
    await reader.read();
    const elapsed = Date.now() - firstChunkAt;
    // Total upstream time is >= 4 * 40ms; the first byte must beat that.
    expect(elapsed).toBeLessThan(150);
    await reader.cancel();
  });

  it("includes a usage chunk when stream_options.include_usage is set", async () => {
    const res = await fetch(`${baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: "claude-haiku-4-5",
        messages: [{ role: "user", content: "hi" }],
        stream: true,
        stream_options: { include_usage: true },
      }),
    });
    const frames = await readSse(res);
    const usageFrame = frames
      .slice(0, -1)
      .map((f) => JSON.parse(f))
      .find((c) => c.usage);
    expect(usageFrame.usage).toEqual({ prompt_tokens: 20, completion_tokens: 9, total_tokens: 29 });
    expect(usageFrame.choices).toEqual([]);
  });

  it("records a trace with final cost after the stream completes", async () => {
    const res = await fetch(`${baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: "claude-haiku-4-5",
        messages: [{ role: "user", content: "hi" }],
        stream: true,
      }),
    });
    await res.text();
    const trace = await traceFor();
    expect(trace.streamed).toBe(true);
    expect(trace.modelServed).toBe("claude-haiku-4-5");
    expect(trace.inputTokens).toBe(20);
    expect(trace.outputTokens).toBe(9);
    // 20 * 1000 + 9 * 5000 = 65_000 nano = $0.000065
    expect(trace.costUsd).toBeCloseTo(0.000065, 10);
    expect(trace.usageIsFinal).toBe(true);
    expect(trace.ttftMs).not.toBeNull();
    expect(trace.finishReason).toBe("stop");
  });
});

describe("POST /v1/chat/completions — mid-stream abort", () => {
  it("terminates the client stream cleanly when upstream dies mid-token", async () => {
    // DESIGN.md failure mode #3: never leave a hanging stream. The client's
    // parser must see a finish_reason and [DONE], not a socket that stops.
    provider = await startMockProvider();
    provider.setScript({
      events: anthropicScript(["one", "two", "three"]),
      destroyAfter: 4,
      delayMs: 5,
    });
    await boot();

    const res = await fetch(`${baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: "claude-haiku-4-5",
        messages: [{ role: "user", content: "hi" }],
        stream: true,
      }),
    });
    const frames = await readSse(res);

    expect(frames.at(-1)).toBe("[DONE]");
    const chunks = frames.slice(0, -1).map((f) => JSON.parse(f));
    expect(chunks.at(-1).choices[0].finish_reason).toBe("length");

    // Partial content is preserved, not discarded.
    const text = chunks.map((c) => c.choices[0]?.delta?.content ?? "").join("");
    expect(text.length).toBeGreaterThan(0);
  });

  it("records the truncation and marks usage as unconfirmed", async () => {
    provider = await startMockProvider();
    provider.setScript({
      events: anthropicScript(["one", "two", "three"]),
      destroyAfter: 4,
      delayMs: 5,
    });
    await boot();

    const res = await fetch(`${baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: "claude-haiku-4-5",
        messages: [{ role: "user", content: "hi" }],
        stream: true,
      }),
    });
    await res.text();

    const trace = await traceFor();
    expect(trace.errorKind).toBe("stream_abort");
    // The provider never sent its final usage, so the recorded cost is a floor.
    // Marking it lets P2 onwards exclude it instead of averaging it in.
    expect(trace.usageIsFinal).toBe(false);
  });
});

describe("POST /v1/chat/completions — client disconnect", () => {
  it("aborts the upstream call so we stop paying for unread tokens", async () => {
    // DESIGN.md failure mode #4. The mock resolves `disconnected` only when it
    // observes the socket closing while it still had events left to send.
    provider = await startMockProvider();
    provider.setScript({
      events: anthropicScript(Array.from({ length: 50 }, (_, i) => `t${i}`)),
      delayMs: 20,
    });
    await boot();

    const ac = new AbortController();
    const res = await fetch(`${baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: "claude-haiku-4-5",
        messages: [{ role: "user", content: "hi" }],
        stream: true,
      }),
      signal: ac.signal,
    });

    const reader = res.body!.getReader();
    await reader.read();
    ac.abort();

    await expect(
      Promise.race([
        provider.disconnected,
        new Promise((_, rej) =>
          setTimeout(() => rej(new Error("upstream was never disconnected")), 3000),
        ),
      ]),
    ).resolves.toBeUndefined();

    const framesAtAbort = provider.framesWritten();
    await new Promise((r) => setTimeout(r, 300));
    // The upstream must have stopped producing; had we kept consuming, the
    // frame count would keep climbing and we would keep being billed.
    expect(provider.framesWritten()).toBeLessThanOrEqual(framesAtAbort + 2);
  });

  it("records the disconnect as client_abort", async () => {
    provider = await startMockProvider();
    provider.setScript({
      events: anthropicScript(Array.from({ length: 50 }, (_, i) => `t${i}`)),
      delayMs: 20,
    });
    await boot();

    const ac = new AbortController();
    const res = await fetch(`${baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: "claude-haiku-4-5",
        messages: [{ role: "user", content: "hi" }],
        stream: true,
      }),
      signal: ac.signal,
    });
    const reader = res.body!.getReader();
    await reader.read();
    ac.abort();

    const trace = await traceFor();
    expect(trace.errorKind).toBe("client_abort");
    expect(trace.status).toBe(499);
  });
});

describe("POST /v1/chat/completions — routing", () => {
  const POLICY = {
    policy_version: 1,
    created_at: "2026-09-15T00:00:00+00:00",
    git_sha: "abc",
    corpus_sha256: "f".repeat(64),
    safe_default: "claude-opus-5",
    floor: 0.9,
    margin: 0.03,
    routes: [
      {
        route_key: "multiple_choice",
        assigned_model: "claude-haiku-4-5",
        n_items: 300,
        quality: 0.96,
        ci_low: 0.93,
        ci_high: 0.98,
        floor: 0.9,
        reason: "cleared the floor",
      },
    ],
    cascade: null,
  };

  let policyPath: string;

  beforeEach(async () => {
    const { mkdtempSync, writeFileSync } = await import("node:fs");
    const { tmpdir } = await import("node:os");
    const { join } = await import("node:path");
    policyPath = join(mkdtempSync(join(tmpdir(), "verdict-p5-")), "policy.json");
    writeFileSync(policyPath, JSON.stringify(POLICY));
    provider.setScript({ events: anthropicScript(["routed"]) });
  });

  const send = (headers: Record<string, string> = {}) =>
    app.inject({
      method: "POST",
      url: "/v1/chat/completions",
      headers,
      payload: { model: "verdict-auto", messages: [{ role: "user", content: "hi" }] },
    });

  it("routes verdict-auto to the policy's model when the flag is on", async () => {
    await boot({ ROUTER_MODE: "offline", POLICY_PATH: policyPath });
    const res = await send({ "x-verdict-route": "multiple_choice" });
    expect(res.headers["x-verdict-model-served"]).toBe("claude-haiku-4-5");
    expect(res.headers["x-verdict-route-reason"]).toBe("policy_route");
  });

  it("serves the safe default when the flag is off, even with a policy loaded", async () => {
    await boot({ ROUTER_MODE: "off" });
    const res = await send({ "x-verdict-route": "multiple_choice" });
    expect(res.headers["x-verdict-model-served"]).toBe("claude-opus-5");
    expect(res.headers["x-verdict-route-reason"]).toBe("router_off");
  });

  it("honours a per-request override that disables the router", async () => {
    // What makes a live bisect possible without a redeploy.
    await boot({ ROUTER_MODE: "offline", POLICY_PATH: policyPath });
    const res = await send({ "x-verdict-route": "multiple_choice", "x-verdict-router": "off" });
    expect(res.headers["x-verdict-model-served"]).toBe("claude-opus-5");
    expect(res.headers["x-verdict-route-reason"]).toBe("router_off");
  });

  it("honours a per-request override that enables the router", async () => {
    await boot({ ROUTER_MODE: "off", POLICY_PATH: policyPath });
    const res = await send({ "x-verdict-route": "multiple_choice", "x-verdict-router": "offline" });
    expect(res.headers["x-verdict-model-served"]).toBe("claude-haiku-4-5");
  });

  it("serves the safe default for a route the policy never saw", async () => {
    await boot({ ROUTER_MODE: "offline", POLICY_PATH: policyPath });
    const res = await send({ "x-verdict-route": "astrology" });
    expect(res.headers["x-verdict-model-served"]).toBe("claude-opus-5");
    expect(res.headers["x-verdict-route-reason"]).toBe("unknown_route");
  });

  it("serves the safe default when no route hint is supplied", async () => {
    await boot({ ROUTER_MODE: "offline", POLICY_PATH: policyPath });
    const res = await send();
    expect(res.headers["x-verdict-model-served"]).toBe("claude-opus-5");
    expect(res.headers["x-verdict-route-reason"]).toBe("no_route_hint");
  });

  it("records the route and policy version on the trace", async () => {
    await boot({ ROUTER_MODE: "offline", POLICY_PATH: policyPath });
    await send({ "x-verdict-route": "multiple_choice" });
    const trace = await traceFor();
    expect(trace.routeKey).toBe("multiple_choice");
    expect(trace.policyVersion).toBe(1);
  });

  it("refuses to boot when the router is on but no policy is configured", async () => {
    // Otherwise it silently serves safe_default forever and looks like it works.
    expect(() => envWith({ ROUTER_MODE: "offline" })).toThrow(/POLICY_PATH/);
  });

  it("refuses to boot on a policy naming a model it cannot serve", async () => {
    const { mkdtempSync, writeFileSync } = await import("node:fs");
    const { tmpdir } = await import("node:os");
    const { join } = await import("node:path");
    const bad = join(mkdtempSync(join(tmpdir(), "verdict-p5-")), "policy.json");
    writeFileSync(
      bad,
      JSON.stringify({ ...POLICY, routes: [{ ...POLICY.routes[0], assigned_model: "gpt-5" }] }),
    );
    // No OPENAI_API_KEY in this environment, so gpt-5 is unservable.
    expect(() =>
      buildServices({
        env: envWith({ ROUTER_MODE: "offline", POLICY_PATH: bad }),
        traceSink: async () => {},
      }),
    ).toThrow(/gpt-5/);
  });

  it("streaming requests still route, and report which path they took", async () => {
    await boot({ ROUTER_MODE: "offline", POLICY_PATH: policyPath });
    const res = await fetch(`${baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-verdict-route": "multiple_choice" },
      body: JSON.stringify({
        model: "verdict-auto",
        messages: [{ role: "user", content: "hi" }],
        stream: true,
      }),
    });
    expect(res.headers.get("x-verdict-model-served")).toBe("claude-haiku-4-5");
    expect(res.headers.get("x-verdict-route")).toBe("multiple_choice");
    await res.text();
  });
});

describe("POST /v1/chat/completions — failure handling", () => {
  it("retries a 500 before any byte reaches the client, then succeeds", async () => {
    provider = await startMockProvider();
    provider.setScript((attempt) =>
      attempt === 0
        ? { failWith: { status: 500, body: { error: { message: "boom" } } } }
        : { events: anthropicScript(["ok"]) },
    );
    await boot({ MAX_RETRIES: "2", RETRY_BASE_DELAY_MS: "1", RETRY_MAX_DELAY_MS: "2" });

    const res = await app.inject({
      method: "POST",
      url: "/v1/chat/completions",
      payload: { model: "claude-haiku-4-5", messages: [{ role: "user", content: "hi" }] },
    });
    expect(res.statusCode).toBe(200);
    expect(provider.requests).toHaveLength(2);
  });

  it("does not retry a 400, because it will fail identically every time", async () => {
    provider = await startMockProvider();
    provider.setScript({ failWith: { status: 400, body: { error: { message: "bad" } } } });
    await boot({ MAX_RETRIES: "3", RETRY_BASE_DELAY_MS: "1", RETRY_MAX_DELAY_MS: "2" });

    const res = await app.inject({
      method: "POST",
      url: "/v1/chat/completions",
      payload: { model: "claude-haiku-4-5", messages: [{ role: "user", content: "hi" }] },
    });
    expect(res.statusCode).toBe(400);
    expect(provider.requests).toHaveLength(1);
  });

  it("maps a 429 to 429 and records rate_limited", async () => {
    provider = await startMockProvider();
    provider.setScript({ failWith: { status: 429, body: { error: { message: "slow down" } } } });
    await boot({ MAX_RETRIES: "0" });

    const res = await app.inject({
      method: "POST",
      url: "/v1/chat/completions",
      payload: { model: "claude-haiku-4-5", messages: [{ role: "user", content: "hi" }] },
    });
    expect(res.statusCode).toBe(429);
    expect((await traceFor()).errorKind).toBe("rate_limited");
  });

  it("opens the breaker after repeated failures and then refuses without calling upstream", async () => {
    provider = await startMockProvider();
    provider.setScript({ failWith: { status: 500, body: { error: { message: "down" } } } });
    await boot({ MAX_RETRIES: "0", BREAKER_FAILURE_THRESHOLD: "2", BREAKER_COOLDOWN_MS: "60000" });

    const send = (): Promise<{ statusCode: number }> =>
      app.inject({
        method: "POST",
        url: "/v1/chat/completions",
        payload: { model: "claude-haiku-4-5", messages: [{ role: "user", content: "hi" }] },
      });

    expect((await send()).statusCode).toBe(502);
    expect((await send()).statusCode).toBe(502);
    const callsBefore = provider.requests.length;

    const third = await send();
    expect(third.statusCode).toBe(503);
    // The whole point: the provider is not touched while the breaker is open.
    expect(provider.requests.length).toBe(callsBefore);
  });

  it("keeps serving when the trace sink is failing", async () => {
    // DESIGN.md failure mode #7: Postgres down degrades observability, not
    // availability.
    provider = await startMockProvider();
    provider.setScript({ events: anthropicScript(["still working"]) });
    const env = envWith({});
    services = buildServices({
      env,
      traceSink: async () => {
        throw new Error("ECONNREFUSED");
      },
    });
    app = buildApp({ env, services });

    const res = await app.inject({
      method: "POST",
      url: "/v1/chat/completions",
      payload: { model: "claude-haiku-4-5", messages: [{ role: "user", content: "hi" }] },
    });
    expect(res.statusCode).toBe(200);
    expect(res.json().choices[0].message.content).toBe("still working");
  });
});
