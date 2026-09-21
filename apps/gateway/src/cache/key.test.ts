/**
 * Cache identity and invalidation.
 *
 * Nearly every test here is about a way the cache could silently serve a wrong
 * answer. That failure mode is invisible at runtime — the response looks
 * completely normal — so it has to be prevented by construction.
 */
import { describe, expect, it } from "vitest";
import { canonicalise, embeddingText, exactKey, expiryFrom, ttlSecondsFor } from "./key.js";

const scope = { model: "claude-haiku-4-5", promptVersion: "v1" };
const msgs = (content: string) => [{ role: "user", content }];

describe("canonicalise", () => {
  it("ignores whitespace differences", () => {
    // Otherwise two identical requests from different clients miss each other
    // and the cache quietly does nothing.
    expect(canonicalise(msgs("what  is\n2+2?"))).toBe(canonicalise(msgs("what is 2+2?")));
  });

  it("ignores role casing and padding", () => {
    expect(canonicalise([{ role: " USER ", content: "hi" }])).toBe(
      canonicalise([{ role: "user", content: "hi" }]),
    );
  });

  it("does NOT ignore content differences", () => {
    expect(canonicalise(msgs("what is 2+2?"))).not.toBe(canonicalise(msgs("what is 2+3?")));
  });

  it("preserves message order", () => {
    const a = canonicalise([
      { role: "user", content: "one" },
      { role: "assistant", content: "two" },
    ]);
    const b = canonicalise([
      { role: "assistant", content: "two" },
      { role: "user", content: "one" },
    ]);
    expect(a).not.toBe(b);
  });
});

describe("exactKey", () => {
  it("is stable for the same request", () => {
    expect(exactKey(msgs("hi"), scope, { temperature: 0 })).toBe(
      exactKey(msgs("hi"), scope, { temperature: 0 }),
    );
  });

  it("is independent of parameter ordering", () => {
    expect(exactKey(msgs("hi"), scope, { a: 1, b: 2 })).toBe(
      exactKey(msgs("hi"), scope, { b: 2, a: 1 }),
    );
  });

  it("changes when the MODEL changes", () => {
    // Two models give different answers; serving one for the other is a false
    // hit dressed up as a cache design.
    expect(exactKey(msgs("hi"), scope, {})).not.toBe(
      exactKey(msgs("hi"), { ...scope, model: "claude-opus-5" }, {}),
    );
  });

  it("changes when the PROMPT VERSION changes", () => {
    // The invalidation mechanism. A bump must make old entries unreachable,
    // not merely stale — nothing about a stored response reveals that the
    // prompt behind it has changed.
    expect(exactKey(msgs("hi"), scope, {})).not.toBe(
      exactKey(msgs("hi"), { ...scope, promptVersion: "v2" }, {}),
    );
  });

  it("changes when sampling parameters change", () => {
    expect(exactKey(msgs("hi"), scope, { temperature: 0 })).not.toBe(
      exactKey(msgs("hi"), scope, { temperature: 1 }),
    );
  });

  it("is namespaced so it cannot collide with other Redis keys", () => {
    expect(exactKey(msgs("hi"), scope, {}).startsWith("verdict:exact:")).toBe(true);
  });
});

describe("embeddingText", () => {
  it("excludes the system prompt", () => {
    // A shared system prompt is identical across every request in a route, so
    // including it drags every similarity toward 1.0 and destroys exactly the
    // discrimination the threshold depends on.
    const withSystem = embeddingText([
      { role: "system", content: "You are a helpful assistant. ".repeat(50) },
      { role: "user", content: "what is 2+2?" },
    ]);
    expect(withSystem).toBe(embeddingText(msgs("what is 2+2?")));
  });

  it("keeps user and assistant turns", () => {
    const text = embeddingText([
      { role: "user", content: "first" },
      { role: "assistant", content: "second" },
    ]);
    expect(text).toContain("first");
    expect(text).toContain("second");
  });

  it("falls back to all messages when there are no user turns", () => {
    // Better to embed something than to embed the empty string, which would
    // make every system-only request a duplicate of every other.
    expect(embeddingText([{ role: "system", content: "only this" }])).toContain("only this");
  });
});

describe("TTL", () => {
  it("uses the default when a route has no override", () => {
    expect(ttlSecondsFor({ defaultSeconds: 3600 }, "multiple_choice")).toBe(3600);
    expect(ttlSecondsFor({ defaultSeconds: 3600 }, null)).toBe(3600);
  });

  it("honours a per-route override", () => {
    const policy = { defaultSeconds: 3600, byRoute: { support_reply: 300 } };
    expect(ttlSecondsFor(policy, "support_reply")).toBe(300);
    expect(ttlSecondsFor(policy, "summarization")).toBe(3600);
  });

  it("allows a zero TTL, meaning do not cache this route", () => {
    expect(ttlSecondsFor({ defaultSeconds: 3600, byRoute: { live_data: 0 } }, "live_data")).toBe(0);
  });

  it("computes an absolute expiry, not a duration", () => {
    // Stored absolute so expiry does not depend on when the sweeper runs.
    const now = new Date("2026-09-21T12:00:00Z");
    expect(expiryFrom(now, 3600).toISOString()).toBe("2026-09-21T13:00:00.000Z");
  });
});
