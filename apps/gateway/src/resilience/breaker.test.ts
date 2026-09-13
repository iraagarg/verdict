import { beforeEach, describe, expect, it } from "vitest";
import { CircuitBreaker, CircuitOpenError } from "./breaker.js";

/** Controllable clock so tests advance time instead of sleeping. */
class Clock {
  t = 0;
  now = (): number => this.t;
  advance(ms: number): void {
    this.t += ms;
  }
}

let clock: Clock;
const make = (threshold = 3, cooldownMs = 1000): CircuitBreaker =>
  new CircuitBreaker("anthropic", { failureThreshold: threshold, cooldownMs, now: clock.now });

beforeEach(() => {
  clock = new Clock();
});

describe("CircuitBreaker state machine", () => {
  it("starts closed and allows attempts", () => {
    const b = make();
    expect(b.state).toBe("closed");
    expect(b.canAttempt()).toBe(true);
  });

  it("stays closed below the failure threshold", () => {
    const b = make(3);
    b.onFailure();
    b.onFailure();
    expect(b.state).toBe("closed");
    expect(b.consecutiveFailures).toBe(2);
  });

  it("opens exactly at the threshold", () => {
    const b = make(3);
    b.onFailure();
    b.onFailure();
    b.onFailure();
    expect(b.state).toBe("open");
  });

  it("refuses attempts while open", () => {
    const b = make(1);
    b.onFailure();
    expect(b.canAttempt()).toBe(false);
  });

  it("resets the failure count on a success, so failures must be consecutive", () => {
    // Two failures, a success, two more failures must NOT trip a threshold of 3.
    const b = make(3);
    b.onFailure();
    b.onFailure();
    b.onSuccess();
    b.onFailure();
    b.onFailure();
    expect(b.state).toBe("closed");
  });

  it("stays open for the whole cooldown", () => {
    const b = make(1, 1000);
    b.onFailure();
    clock.advance(999);
    expect(b.state).toBe("open");
    expect(b.canAttempt()).toBe(false);
  });

  it("moves to half-open once the cooldown elapses", () => {
    const b = make(1, 1000);
    b.onFailure();
    clock.advance(1000);
    expect(b.state).toBe("half-open");
  });

  it("allows exactly one probe in half-open", () => {
    // Without this, every queued request stampedes a provider that is probably
    // still unhealthy the instant the cooldown expires.
    const b = make(1, 1000);
    b.onFailure();
    clock.advance(1000);
    expect(b.canAttempt()).toBe(true);
    expect(b.canAttempt()).toBe(false);
    expect(b.canAttempt()).toBe(false);
  });

  it("closes on a successful probe and forgets prior failures", () => {
    const b = make(3, 1000);
    b.onFailure();
    b.onFailure();
    b.onFailure();
    clock.advance(1000);
    expect(b.canAttempt()).toBe(true);
    b.onSuccess();
    expect(b.state).toBe("closed");
    expect(b.consecutiveFailures).toBe(0);
    expect(b.canAttempt()).toBe(true);
  });

  it("re-opens immediately on a failed probe without re-accumulating failures", () => {
    const b = make(3, 1000);
    b.onFailure();
    b.onFailure();
    b.onFailure();
    clock.advance(1000);
    b.canAttempt();
    b.onFailure(); // single probe failure
    expect(b.state).toBe("open");
  });

  it("restarts the full cooldown after a failed probe", () => {
    const b = make(1, 1000);
    b.onFailure();
    clock.advance(1000);
    b.canAttempt();
    b.onFailure();
    clock.advance(999);
    expect(b.state).toBe("open");
    clock.advance(1);
    expect(b.state).toBe("half-open");
  });

  it("permits a fresh probe after each cooldown", () => {
    const b = make(1, 100);
    b.onFailure();
    for (let i = 0; i < 3; i++) {
      clock.advance(100);
      expect(b.canAttempt(), `probe ${i}`).toBe(true);
      b.onFailure();
      expect(b.state).toBe("open");
    }
  });

  it("reports how long until the next probe", () => {
    const b = make(1, 1000);
    b.onFailure();
    expect(b.retryAfterMs()).toBe(1000);
    clock.advance(400);
    expect(b.retryAfterMs()).toBe(600);
    clock.advance(600);
    expect(b.retryAfterMs()).toBe(0);
  });

  it("reports zero retry-after while closed", () => {
    expect(make().retryAfterMs()).toBe(0);
  });

  it("throws a typed error carrying the provider and retry delay", () => {
    const b = make(1, 5000);
    b.onFailure();
    expect(() => b.assertCanAttempt()).toThrow(CircuitOpenError);
    try {
      b.assertCanAttempt();
    } catch (err) {
      expect(err).toBeInstanceOf(CircuitOpenError);
      const e = err as CircuitOpenError;
      expect(e.provider).toBe("anthropic");
      expect(e.retryAfterMs).toBe(5000);
    }
  });

  it("does not throw while closed", () => {
    expect(() => make().assertCanAttempt()).not.toThrow();
  });

  it("can be force-reset", () => {
    const b = make(1, 10_000);
    b.onFailure();
    expect(b.state).toBe("open");
    b.reset();
    expect(b.state).toBe("closed");
    expect(b.canAttempt()).toBe(true);
  });

  it("handles a zero cooldown by going half-open immediately", () => {
    const b = make(1, 0);
    b.onFailure();
    expect(b.state).toBe("half-open");
  });

  it("rejects nonsensical configuration at construction", () => {
    expect(() => new CircuitBreaker("x", { failureThreshold: 0, cooldownMs: 1 })).toThrow();
    expect(() => new CircuitBreaker("x", { failureThreshold: 1, cooldownMs: -1 })).toThrow();
  });

  it("releases the probe slot when a probe fails, so the next cooldown works", () => {
    const b = make(1, 100);
    b.onFailure();
    clock.advance(100);
    b.canAttempt();
    b.onFailure();
    clock.advance(100);
    expect(b.canAttempt()).toBe(true);
  });
});
