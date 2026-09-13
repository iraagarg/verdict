import { describe, expect, it } from "vitest";
import { EnvValidationError, loadEnv } from "./env.js";

const BASE: NodeJS.ProcessEnv = {
  DATABASE_URL: "postgres://verdict:verdict@localhost:5432/verdict",
  REDIS_URL: "redis://localhost:6379",
  ANTHROPIC_API_KEY: "sk-ant-test-abc123",
  COST_CAP_USD_PER_DAY: "5.00",
};

const withEnv = (overrides: NodeJS.ProcessEnv) => ({ ...BASE, ...overrides });

describe("loadEnv", () => {
  it("accepts a minimal valid environment and applies defaults", () => {
    const env = loadEnv(BASE);
    expect(env.NODE_ENV).toBe("development");
    expect(env.LOG_LEVEL).toBe("info");
    expect(env.GATEWAY_PORT).toBe(8080);
    expect(env.GATEWAY_HOST).toBe("0.0.0.0");
  });

  it("coerces numeric strings, because every env var arrives as a string", () => {
    const env = loadEnv(withEnv({ GATEWAY_PORT: "3001", COST_CAP_USD_PER_DAY: "12.5" }));
    expect(env.GATEWAY_PORT).toBe(3001);
    expect(env.COST_CAP_USD_PER_DAY).toBe(12.5);
  });

  it("refuses to start when no provider key is set", () => {
    const { ANTHROPIC_API_KEY: _drop, ...rest } = BASE;
    expect(() => loadEnv(rest)).toThrow(EnvValidationError);
    expect(() => loadEnv(rest)).toThrow(/at least one of/);
  });

  it("accepts any single provider key", () => {
    const { ANTHROPIC_API_KEY: _drop, ...rest } = BASE;
    expect(() => loadEnv({ ...rest, GROQ_API_KEY: "gsk-real-key" })).not.toThrow();
    expect(() => loadEnv({ ...rest, OPENAI_API_KEY: "sk-real-key" })).not.toThrow();
  });

  it("rejects a placeholder key left over from .env.example", () => {
    // A present-but-fake key is worse than an absent one: it passes a naive
    // presence check and then fails at request time, which is exactly what
    // non-negotiable #6 forbids.
    for (const fake of ["your-key-here", "changeme", "<paste-key>", "TODO"]) {
      expect(() => loadEnv(withEnv({ ANTHROPIC_API_KEY: fake })), fake).toThrow(/placeholder/);
    }
  });

  it("treats a blank key as unset, not as a validation failure", () => {
    // docker compose passes `FOO: ${FOO:-}` as "" for every variable the
    // operator did not set. Rejecting "" would stop the stack from starting
    // just because the user has no Groq account.
    expect(() => loadEnv(withEnv({ OPENAI_API_KEY: "", GROQ_API_KEY: "  " }))).not.toThrow();
    const env = loadEnv(withEnv({ OPENAI_API_KEY: "", GROQ_API_KEY: "  " }));
    expect(env.OPENAI_API_KEY).toBeUndefined();
    expect(env.GROQ_API_KEY).toBeUndefined();
  });

  it("still refuses to start when every provider key is blank", () => {
    expect(() =>
      loadEnv({ ...BASE, ANTHROPIC_API_KEY: "", OPENAI_API_KEY: "", GROQ_API_KEY: "   " }),
    ).toThrow(/at least one of/);
  });

  it("rejects a DATABASE_URL that is not postgres", () => {
    expect(() => loadEnv(withEnv({ DATABASE_URL: "mysql://localhost:3306/verdict" }))).toThrow(
      /postgres/,
    );
  });

  it("rejects a REDIS_URL that is not redis", () => {
    expect(() => loadEnv(withEnv({ REDIS_URL: "http://localhost:6379" }))).toThrow(/redis/);
  });

  it("accepts rediss:// and postgresql:// for managed providers", () => {
    expect(() =>
      loadEnv(
        withEnv({
          REDIS_URL: "rediss://default:token@some-host.upstash.io:6379",
          DATABASE_URL: "postgresql://user:pw@ep-x.neon.tech/verdict?sslmode=require",
        }),
      ),
    ).not.toThrow();
  });

  it("rejects an out-of-range port", () => {
    expect(() => loadEnv(withEnv({ GATEWAY_PORT: "70000" }))).toThrow(EnvValidationError);
    expect(() => loadEnv(withEnv({ GATEWAY_PORT: "0" }))).toThrow(EnvValidationError);
    expect(() => loadEnv(withEnv({ GATEWAY_PORT: "not-a-port" }))).toThrow(EnvValidationError);
  });

  it("rejects a non-positive cost cap", () => {
    expect(() => loadEnv(withEnv({ COST_CAP_USD_PER_DAY: "0" }))).toThrow(EnvValidationError);
    expect(() => loadEnv(withEnv({ COST_CAP_USD_PER_DAY: "-1" }))).toThrow(EnvValidationError);
  });

  it("requires a cost cap — an unbounded gateway can spend without limit", () => {
    const { COST_CAP_USD_PER_DAY: _drop, ...rest } = BASE;
    expect(() => loadEnv(rest)).toThrow(/COST_CAP_USD_PER_DAY/);
  });

  it("rejects an invalid LOG_LEVEL instead of silently falling back", () => {
    expect(() => loadEnv(withEnv({ LOG_LEVEL: "verbose" }))).toThrow(EnvValidationError);
  });

  it("never leaks a secret value into the error message", () => {
    const secret = "sk-ant-super-secret-value-9f3a2b";
    let message = "";
    try {
      // Invalid URL forces a failure while a real-looking secret is present.
      loadEnv(withEnv({ ANTHROPIC_API_KEY: secret, DATABASE_URL: "not-a-url" }));
    } catch (err) {
      message = (err as Error).message;
    }
    expect(message).not.toContain(secret);
    expect(message).toContain("DATABASE_URL");
  });

  it("redacts the value of a failing secret variable but still names it", () => {
    let message = "";
    try {
      loadEnv(withEnv({ DATABASE_URL: "mysql://oops" }));
    } catch (err) {
      message = (err as Error).message;
    }
    expect(message).toContain("DATABASE_URL");
    expect(message).toContain("value redacted");
    expect(message).not.toContain("mysql://oops");
  });

  it("reports every problem at once rather than one per restart", () => {
    let message = "";
    try {
      loadEnv({ ...BASE, DATABASE_URL: "nope", REDIS_URL: "nope", COST_CAP_USD_PER_DAY: "-5" });
    } catch (err) {
      message = (err as Error).message;
    }
    expect(message).toContain("DATABASE_URL");
    expect(message).toContain("REDIS_URL");
    expect(message).toContain("COST_CAP_USD_PER_DAY");
  });
});
