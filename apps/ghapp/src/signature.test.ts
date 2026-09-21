/**
 * Signature verification.
 *
 * This endpoint is public, so the signature is the only thing distinguishing a
 * real delivery from an attacker's. Every test below is a specific attack or a
 * specific way the check could be quietly weakened.
 */
import { describe, expect, it } from "vitest";
import { createHmac } from "node:crypto";
import { DeliveryLog, touchesPrompts, verifySignature } from "./signature.js";

const SECRET = "a-sufficiently-long-webhook-secret";
const sign = (body: string, secret = SECRET) =>
  `sha256=${createHmac("sha256", secret).update(body).digest("hex")}`;

describe("verifySignature", () => {
  const body = JSON.stringify({ action: "opened", number: 7 });

  it("accepts a correctly signed payload", () => {
    expect(verifySignature(body, sign(body), SECRET)).toEqual({ ok: true });
  });

  it("accepts a Buffer body identically to a string", () => {
    expect(verifySignature(Buffer.from(body, "utf8"), sign(body), SECRET)).toEqual({ ok: true });
  });

  it("rejects a payload signed with a different secret", () => {
    expect(verifySignature(body, sign(body, "attacker-secret-value"), SECRET)).toEqual({
      ok: false,
      reason: "bad_signature",
    });
  });

  it("rejects a tampered body", () => {
    // The attack the signature exists to stop: capture a real delivery, edit it.
    const signature = sign(body);
    const tampered = JSON.stringify({ action: "opened", number: 99 });
    expect(verifySignature(tampered, signature, SECRET)).toEqual({
      ok: false,
      reason: "bad_signature",
    });
  });

  it("rejects a single flipped byte", () => {
    const sig = sign(body);
    const flipped = `${sig.slice(0, -1)}${sig.at(-1) === "a" ? "b" : "a"}`;
    expect(verifySignature(body, flipped, SECRET).ok).toBe(false);
  });

  it("rejects a missing signature header", () => {
    expect(verifySignature(body, undefined, SECRET)).toEqual({
      ok: false,
      reason: "missing_signature",
    });
    expect(verifySignature(body, "", SECRET)).toEqual({ ok: false, reason: "missing_signature" });
  });

  it("rejects a signature with no sha256= prefix", () => {
    const bare = sign(body).slice("sha256=".length);
    expect(verifySignature(body, bare, SECRET)).toEqual({
      ok: false,
      reason: "malformed_signature",
    });
  });

  it("rejects a wrong-length or non-hex digest without throwing", () => {
    // timingSafeEqual throws on a length mismatch, and that throw would itself
    // leak the expected digest length. The shape is validated first.
    for (const bad of ["sha256=abc", "sha256=" + "z".repeat(64), "sha256=" + "a".repeat(63)]) {
      expect(() => verifySignature(body, bad, SECRET)).not.toThrow();
      expect(verifySignature(body, bad, SECRET)).toEqual({
        ok: false,
        reason: "malformed_signature",
      });
    }
  });

  it("accepts an uppercase hex digest", () => {
    expect(
      verifySignature(body, sign(body).toUpperCase().replace("SHA256=", "sha256="), SECRET),
    ).toEqual({ ok: true });
  });

  it("refuses to verify at all when no secret is configured", () => {
    // Must NOT fall through to "no secret means no checking".
    expect(verifySignature(body, sign(body), "")).toEqual({ ok: false, reason: "missing_secret" });
  });

  it("is sensitive to whitespace, because the raw bytes are what is signed", () => {
    // Why the handler keeps the raw buffer: JSON.parse then JSON.stringify can
    // change bytes, and the re-serialised payload would fail its own signature.
    const reserialised = JSON.stringify(JSON.parse(`{ "action":  "opened" , "number": 7 }`));
    const original = `{ "action":  "opened" , "number": 7 }`;
    expect(verifySignature(reserialised, sign(original), SECRET).ok).toBe(false);
  });

  it("verifies an empty body correctly rather than short-circuiting", () => {
    expect(verifySignature("", sign(""), SECRET)).toEqual({ ok: true });
  });
});

describe("DeliveryLog", () => {
  it("accepts a delivery once", () => {
    const log = new DeliveryLog();
    expect(log.accept("d1")).toBe(true);
  });

  it("rejects a replay of the same delivery", () => {
    // A signature stays valid forever, so a captured delivery can be resent
    // and is indistinguishable by signature alone.
    const log = new DeliveryLog();
    log.accept("d1");
    expect(log.accept("d1")).toBe(false);
  });

  it("makes GitHub's own retries idempotent", () => {
    const log = new DeliveryLog();
    expect([log.accept("retry"), log.accept("retry"), log.accept("retry")]).toEqual([
      true,
      false,
      false,
    ]);
  });

  it("accepts distinct deliveries", () => {
    const log = new DeliveryLog();
    expect([log.accept("a"), log.accept("b")]).toEqual([true, true]);
  });

  it("is bounded, so an attacker cannot grow it without limit", () => {
    const log = new DeliveryLog(50);
    for (let i = 0; i < 500; i++) log.accept(`d${i}`);
    expect(log.size).toBeLessThanOrEqual(50);
  });

  it("forgets entries past the TTL", () => {
    let now = 0;
    const log = new DeliveryLog(1000, 1000, () => now);
    log.accept("old");
    now = 2000;
    expect(log.accept("old")).toBe(true); // expired, so treated as new
  });

  it("still blocks a replay inside the TTL", () => {
    let now = 0;
    const log = new DeliveryLog(1000, 10_000, () => now);
    log.accept("fresh");
    now = 5000;
    expect(log.accept("fresh")).toBe(false);
  });
});

describe("touchesPrompts", () => {
  it("matches a changed prompt file", () => {
    expect(touchesPrompts(["README.md", "prompts/judge.md"])).toBe(true);
  });

  it("ignores a PR that changes no prompts", () => {
    // Evals cost money; a docs-only PR must not trigger one.
    expect(touchesPrompts(["README.md", "src/app.ts"])).toBe(false);
  });

  it("does not match a lookalike path", () => {
    expect(touchesPrompts(["docs/prompts/notes.md", "myprompts/x.md"])).toBe(false);
  });

  it("honours a custom prefix", () => {
    expect(touchesPrompts(["config/prompts/a.md"], "config/prompts/")).toBe(true);
  });

  it("handles an empty change list", () => {
    expect(touchesPrompts([])).toBe(false);
  });
});
