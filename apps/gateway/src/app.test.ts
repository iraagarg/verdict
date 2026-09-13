import { describe, expect, it, afterEach } from "vitest";
import { buildApp, type GatewayApp } from "./app.js";
import { loadEnv } from "./env.js";

const env = loadEnv({
  DATABASE_URL: "postgres://verdict:verdict@localhost:5432/verdict",
  REDIS_URL: "redis://localhost:6379",
  ANTHROPIC_API_KEY: "sk-ant-test-abc123",
  COST_CAP_USD_PER_DAY: "5.00",
  LOG_LEVEL: "fatal", // keep the test output readable
});

let app: GatewayApp | undefined;

afterEach(async () => {
  await app?.close();
  app = undefined;
});

describe("gateway app", () => {
  it("serves liveness without touching a datastore", async () => {
    // Postgres and Redis are not running in this test. /health must still pass:
    // a datastore outage is not a reason to kill the process.
    app = buildApp({ env });
    const res = await app.inject({ method: "GET", url: "/health" });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({ status: "ok", service: "gateway" });
  });

  it("serves readiness", async () => {
    app = buildApp({ env });
    const res = await app.inject({ method: "GET", url: "/ready" });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({ status: "ready" });
  });

  it("returns a request id on every response", async () => {
    app = buildApp({ env });
    const res = await app.inject({ method: "GET", url: "/health" });
    expect(res.headers["x-verdict-request-id"]).toBeTruthy();
  });

  it("propagates an inbound x-request-id so traces span services", async () => {
    app = buildApp({ env });
    const res = await app.inject({
      method: "GET",
      url: "/health",
      headers: { "x-request-id": "trace-from-dashboard-42" },
    });
    expect(res.headers["x-verdict-request-id"]).toBe("trace-from-dashboard-42");
  });

  it("mints a fresh id when the inbound one is absurdly long", async () => {
    app = buildApp({ env });
    const res = await app.inject({
      method: "GET",
      url: "/health",
      headers: { "x-request-id": "x".repeat(5000) },
    });
    expect(res.headers["x-verdict-request-id"]).not.toBe("x".repeat(5000));
    expect(res.headers["x-verdict-request-id"]).toBeTruthy();
  });

  it("gives distinct ids to distinct requests", async () => {
    app = buildApp({ env });
    const [a, b] = await Promise.all([
      app.inject({ method: "GET", url: "/health" }),
      app.inject({ method: "GET", url: "/health" }),
    ]);
    expect(a.headers["x-verdict-request-id"]).not.toBe(b.headers["x-verdict-request-id"]);
  });

  it("returns an OpenAI-shaped error for an unknown route", async () => {
    app = buildApp({ env });
    const res = await app.inject({ method: "GET", url: "/v1/nope" });
    expect(res.statusCode).toBe(404);
    expect(res.json()).toMatchObject({
      error: { type: "invalid_request_error", code: "not_found" },
    });
  });

  it("does not yet implement chat completions — that is P1", async () => {
    // This test documents the current boundary and will be replaced in P1.
    app = buildApp({ env });
    const res = await app.inject({ method: "POST", url: "/v1/chat/completions", payload: {} });
    expect(res.statusCode).toBe(404);
  });
});
