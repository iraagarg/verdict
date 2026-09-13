import { describe, expect, it, vi } from "vitest";
import { AbortedError, backoffDelay, withRetry } from "./retry.js";

const retryable = (): boolean => true;
const noSleep = async (): Promise<void> => {};

describe("backoffDelay", () => {
  it("grows exponentially with the attempt number", () => {
    const always1 = (): number => 0.999999;
    expect(backoffDelay(0, 100, 10_000, always1)).toBeLessThan(100);
    expect(backoffDelay(1, 100, 10_000, always1)).toBeLessThan(200);
    expect(backoffDelay(2, 100, 10_000, always1)).toBeLessThan(400);
    expect(backoffDelay(3, 100, 10_000, always1)).toBeLessThan(800);
  });

  it("is capped by maxDelayMs", () => {
    const always1 = (): number => 0.999999;
    expect(backoffDelay(20, 100, 5_000, always1)).toBeLessThanOrEqual(5_000);
  });

  it("uses FULL jitter: the floor is zero, not the backoff value", () => {
    // Full jitter is what actually decorrelates a herd. Capped exponential
    // alone has every client retrying on the same millisecond after an outage.
    expect(backoffDelay(5, 100, 10_000, () => 0)).toBe(0);
  });

  it("spans the whole range across many draws", () => {
    const draws = Array.from({ length: 1000 }, (_, i) =>
      backoffDelay(3, 100, 10_000, () => i / 1000),
    );
    expect(Math.min(...draws)).toBe(0);
    expect(Math.max(...draws)).toBeGreaterThan(700);
    expect(Math.max(...draws)).toBeLessThan(800);
  });
});

describe("withRetry", () => {
  it("returns the first successful result without sleeping", async () => {
    const fn = vi.fn(async () => "ok");
    const sleep = vi.fn(noSleep);
    const out = await withRetry(fn, {
      retries: 2,
      baseDelayMs: 10,
      maxDelayMs: 100,
      isRetryable: retryable,
      sleep,
    });
    expect(out).toBe("ok");
    expect(fn).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
  });

  it("retries up to the limit then rethrows the last error", async () => {
    const fn = vi.fn(async () => {
      throw new Error("boom");
    });
    await expect(
      withRetry(fn, {
        retries: 2,
        baseDelayMs: 10,
        maxDelayMs: 100,
        isRetryable: retryable,
        sleep: noSleep,
      }),
    ).rejects.toThrow("boom");
    expect(fn).toHaveBeenCalledTimes(3); // 1 initial + 2 retries
  });

  it("succeeds on a later attempt", async () => {
    let calls = 0;
    const out = await withRetry(
      async () => {
        if (++calls < 3) throw new Error("transient");
        return "recovered";
      },
      { retries: 3, baseDelayMs: 1, maxDelayMs: 10, isRetryable: retryable, sleep: noSleep },
    );
    expect(out).toBe("recovered");
    expect(calls).toBe(3);
  });

  it("does not retry an error classified as non-retryable", async () => {
    // A 400 will fail identically every time; retrying wastes the user's latency.
    const fn = vi.fn(async () => {
      throw new Error("bad request");
    });
    await expect(
      withRetry(fn, {
        retries: 5,
        baseDelayMs: 1,
        maxDelayMs: 10,
        isRetryable: () => false,
        sleep: noSleep,
      }),
    ).rejects.toThrow("bad request");
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("passes the attempt index to the callee", async () => {
    const seen: number[] = [];
    await withRetry(
      async (attempt) => {
        seen.push(attempt);
        if (attempt < 2) throw new Error("again");
        return "done";
      },
      { retries: 3, baseDelayMs: 1, maxDelayMs: 10, isRetryable: retryable, sleep: noSleep },
    );
    expect(seen).toEqual([0, 1, 2]);
  });

  it("honours a provider Retry-After instead of its own curve", async () => {
    const delays: number[] = [];
    let calls = 0;
    await withRetry(
      async () => {
        if (++calls < 2) throw new Error("429");
        return "ok";
      },
      {
        retries: 2,
        baseDelayMs: 10,
        maxDelayMs: 60_000,
        isRetryable: retryable,
        retryAfterMs: () => 2_000,
        random: () => 0,
        sleep: async (ms) => {
          delays.push(ms);
        },
      },
    );
    expect(delays).toEqual([2_000]);
  });

  it("caps an absurd Retry-After at maxDelayMs", async () => {
    const delays: number[] = [];
    let calls = 0;
    await withRetry(
      async () => {
        if (++calls < 2) throw new Error("429");
        return "ok";
      },
      {
        retries: 2,
        baseDelayMs: 10,
        maxDelayMs: 5_000,
        isRetryable: retryable,
        retryAfterMs: () => 3_600_000,
        random: () => 0,
        sleep: async (ms) => {
          delays.push(ms);
        },
      },
    );
    expect(delays).toEqual([5_000]);
  });

  it("reports each retry so it can be logged and counted", async () => {
    const onRetry = vi.fn();
    let calls = 0;
    await withRetry(
      async () => {
        if (++calls < 3) throw new Error("x");
        return 1;
      },
      {
        retries: 3,
        baseDelayMs: 1,
        maxDelayMs: 10,
        isRetryable: retryable,
        sleep: noSleep,
        onRetry,
      },
    );
    expect(onRetry).toHaveBeenCalledTimes(2);
    expect(onRetry.mock.calls[0]?.[0]).toMatchObject({ attempt: 0 });
  });

  it("stops immediately when the signal is already aborted", async () => {
    const fn = vi.fn(async () => "never");
    const ac = new AbortController();
    ac.abort();
    await expect(
      withRetry(fn, {
        retries: 3,
        baseDelayMs: 1,
        maxDelayMs: 10,
        isRetryable: retryable,
        signal: ac.signal,
        sleep: noSleep,
      }),
    ).rejects.toThrow(AbortedError);
    expect(fn).not.toHaveBeenCalled();
  });

  it("stops retrying once the signal aborts mid-flight", async () => {
    // The client hung up. Continuing to retry spends money on a response
    // nobody will read.
    const ac = new AbortController();
    let calls = 0;
    await expect(
      withRetry(
        async () => {
          calls++;
          ac.abort();
          throw new Error("boom");
        },
        {
          retries: 5,
          baseDelayMs: 1,
          maxDelayMs: 10,
          isRetryable: retryable,
          signal: ac.signal,
          sleep: noSleep,
        },
      ),
    ).rejects.toThrow();
    expect(calls).toBe(1);
  });

  it("never retries an AbortedError", async () => {
    const fn = vi.fn(async () => {
      throw new AbortedError();
    });
    await expect(
      withRetry(fn, {
        retries: 3,
        baseDelayMs: 1,
        maxDelayMs: 10,
        isRetryable: retryable,
        sleep: noSleep,
      }),
    ).rejects.toThrow(AbortedError);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("makes exactly one call when retries is zero", async () => {
    const fn = vi.fn(async () => {
      throw new Error("once");
    });
    await expect(
      withRetry(fn, {
        retries: 0,
        baseDelayMs: 1,
        maxDelayMs: 10,
        isRetryable: retryable,
        sleep: noSleep,
      }),
    ).rejects.toThrow("once");
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
