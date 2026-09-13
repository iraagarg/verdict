import { describe, expect, it, afterEach } from "vitest";
import { buildApp, type GatewayApp } from "./app.js";
import { buildServices, type GatewayServices } from "./services.js";
import { loadEnv } from "./env.js";

const env = loadEnv({
  DATABASE_URL: "postgres://verdict:verdict@localhost:5432/verdict",
  REDIS_URL: "redis://localhost:6379",
  ANTHROPIC_API_KEY: "sk-ant-test-abc123",
  COST_CAP_USD_PER_DAY: "5.00",
  LOG_LEVEL: "fatal", // keep the test output readable
  MODELS_CONFIG_PATH: "../../config/models.yaml",
});

/** In-memory trace sink: these tests must not need a database. */
function makeServices(): GatewayServices {
  return buildServices({ env, traceSink: async () => {} });
}

let app: GatewayApp | undefined;
let services: GatewayServices | undefined;

function boot(): GatewayApp {
  services = makeServices();
  app = buildApp({ env, services });
  return app;
}

afterEach(async () => {
  await app?.close();
  await services?.close();
  app = undefined;
  services = undefined;
});

describe("gateway app", () => {
  it("serves liveness without touching a datastore", async () => {
    // Postgres and Redis are not running in this test. /health must still pass:
    // a datastore outage is not a reason to kill the process.
    boot();
    const res = await app!.inject({ method: "GET", url: "/health" });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({ status: "ok", service: "gateway" });
  });

  it("serves readiness", async () => {
    boot();
    const res = await app!.inject({ method: "GET", url: "/ready" });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({ status: "ready" });
  });

  it("returns a request id on every response", async () => {
    boot();
    const res = await app!.inject({ method: "GET", url: "/health" });
    expect(res.headers["x-verdict-request-id"]).toBeTruthy();
  });

  it("propagates an inbound x-request-id so traces span services", async () => {
    boot();
    const res = await app!.inject({
      method: "GET",
      url: "/health",
      headers: { "x-request-id": "trace-from-dashboard-42" },
    });
    expect(res.headers["x-verdict-request-id"]).toBe("trace-from-dashboard-42");
  });

  it("mints a fresh id when the inbound one is absurdly long", async () => {
    boot();
    const res = await app!.inject({
      method: "GET",
      url: "/health",
      headers: { "x-request-id": "x".repeat(5000) },
    });
    expect(res.headers["x-verdict-request-id"]).not.toBe("x".repeat(5000));
    expect(res.headers["x-verdict-request-id"]).toBeTruthy();
  });

  it("gives distinct ids to distinct requests", async () => {
    boot();
    const [a, b] = await Promise.all([
      app!.inject({ method: "GET", url: "/health" }),
      app!.inject({ method: "GET", url: "/health" }),
    ]);
    expect(a.headers["x-verdict-request-id"]).not.toBe(b.headers["x-verdict-request-id"]);
  });

  it("returns an OpenAI-shaped error for an unknown route", async () => {
    boot();
    const res = await app!.inject({ method: "GET", url: "/v1/nope" });
    expect(res.statusCode).toBe(404);
    expect(res.json()).toMatchObject({
      error: { type: "invalid_request_error", code: "not_found" },
    });
  });

  it("exposes the chat completions endpoint and validates its body", async () => {
    // P0 asserted a 404 here. P1 implements the route, so an empty payload must
    // now be a validation error rather than a missing route.
    boot();
    const res = await app!.inject({ method: "POST", url: "/v1/chat/completions", payload: {} });
    expect(res.statusCode).toBe(400);
    expect(res.json()).toMatchObject({ error: { type: "invalid_request_error" } });
  });

  it("reports trace-queue and breaker health on /ready", async () => {
    boot();
    const res = await app!.inject({ method: "GET", url: "/ready" });
    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.dependencies).toMatchObject({ traces_queued: 0, traces_dropped: 0 });
    expect(body.dependencies.breakers).toHaveProperty("anthropic", "closed");
  });
});
