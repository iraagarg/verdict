import { describe, expect, it } from "vitest";
import Anthropic from "@anthropic-ai/sdk";
import OpenAI from "openai";
import { classifyAnthropicError } from "./anthropic.js";
import { classifyOpenAIError } from "./openai.js";

/**
 * The circuit breaker only exists to stop us calling a BROKEN provider.
 * These tests pin down which failures are evidence of that and which are not,
 * because getting it wrong silently destroys throughput: a single burst of
 * rate limits once opened the breaker and turned 339 queued requests into
 * instant 503s.
 */
const headers = (h: Record<string, string> = {}): Headers => new Headers(h);

describe("failure classification", () => {
  it("does NOT open the breaker on a rate limit", () => {
    const anthropic = classifyAnthropicError(
      new Anthropic.RateLimitError(429, undefined, "slow down", headers()),
    );
    const openai = classifyOpenAIError(
      new OpenAI.RateLimitError(429, undefined, "slow down", headers()),
    );
    for (const f of [anthropic, openai]) {
      expect(f.kind).toBe("rate_limited");
      expect(f.retryable, "a 429 is worth retrying").toBe(true);
      expect(f.countsTowardBreaker, "a 429 means healthy-but-throttled").toBe(false);
    }
  });

  it("reads Retry-After so we obey the provider's own pacing", () => {
    const f = classifyOpenAIError(
      new OpenAI.RateLimitError(429, undefined, "slow", headers({ "retry-after": "12" })),
    );
    expect(f.retryAfterMs).toBe(12_000);
  });

  it("opens the breaker on a 5xx, which is real ill health", () => {
    const f = classifyOpenAIError(
      new OpenAI.InternalServerError(503, undefined, "down", headers()),
    );
    expect(f.kind).toBe("provider_5xx");
    expect(f.retryable).toBe(true);
    expect(f.countsTowardBreaker).toBe(true);
  });

  it("opens the breaker on an auth failure, which fails every request until fixed", () => {
    const f = classifyAnthropicError(
      new Anthropic.AuthenticationError(401, undefined, "bad key", headers()),
    );
    expect(f.retryable).toBe(false);
    expect(f.countsTowardBreaker).toBe(true);
  });

  it("does NOT open the breaker on a 400, which is our bug not theirs", () => {
    const f = classifyOpenAIError(new OpenAI.BadRequestError(400, undefined, "nope", headers()));
    expect(f.kind).toBe("bad_request");
    expect(f.retryable).toBe(false);
    expect(f.countsTowardBreaker).toBe(false);
  });

  it("opens the breaker on a connection failure", () => {
    const f = classifyOpenAIError(new OpenAI.APIConnectionError({ message: "ECONNREFUSED" }));
    expect(f.countsTowardBreaker).toBe(true);
  });
});
