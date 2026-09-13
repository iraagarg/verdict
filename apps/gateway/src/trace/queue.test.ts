import { describe, expect, it, vi } from "vitest";
import { TraceQueue } from "./queue.js";

const settle = (): Promise<void> => new Promise((r) => setImmediate(r));

describe("TraceQueue", () => {
  it("buffers rows without calling the sink until the batch is full", async () => {
    const sink = vi.fn(async () => {});
    const q = new TraceQueue<number>({ capacity: 10, batchSize: 3, flushIntervalMs: 1000, sink });
    q.enqueue(1);
    q.enqueue(2);
    await settle();
    expect(sink).not.toHaveBeenCalled();
    expect(q.size).toBe(2);
  });

  it("flushes automatically when the batch size is reached", async () => {
    const sink = vi.fn(async () => {});
    const q = new TraceQueue<number>({ capacity: 10, batchSize: 3, flushIntervalMs: 1000, sink });
    q.enqueue(1);
    q.enqueue(2);
    q.enqueue(3);
    await settle();
    expect(sink).toHaveBeenCalledWith([1, 2, 3]);
    expect(q.size).toBe(0);
  });

  it("never blocks the caller", () => {
    // enqueue is called from the request path; it must be synchronous and safe.
    const q = new TraceQueue<number>({
      capacity: 2,
      batchSize: 10,
      flushIntervalMs: 1000,
      sink: async () => {
        throw new Error("db down");
      },
    });
    expect(() => {
      q.enqueue(1);
      q.enqueue(2);
      q.enqueue(3);
    }).not.toThrow();
  });

  it("drops the OLDEST row when at capacity and counts the loss", () => {
    const onDrop = vi.fn();
    const q = new TraceQueue<number>({
      capacity: 3,
      batchSize: 100,
      flushIntervalMs: 1000,
      sink: async () => {},
      onDrop,
    });
    for (const n of [1, 2, 3, 4, 5]) q.enqueue(n);
    expect(q.size).toBe(3);
    expect(q.dropped).toBe(2);
    expect(onDrop).toHaveBeenCalled();
  });

  it("keeps the most recent rows when dropping", async () => {
    const rows: number[][] = [];
    const q = new TraceQueue<number>({
      capacity: 3,
      batchSize: 100,
      flushIntervalMs: 1000,
      sink: async (b) => {
        rows.push(b);
      },
    });
    for (const n of [1, 2, 3, 4, 5]) q.enqueue(n);
    await q.flush();
    expect(rows[0]).toEqual([3, 4, 5]);
  });

  it("reports whether a row was accepted", () => {
    const q = new TraceQueue<number>({
      capacity: 1,
      batchSize: 100,
      flushIntervalMs: 1000,
      sink: async () => {},
    });
    expect(q.enqueue(1)).toBe(true);
    expect(q.enqueue(2)).toBe(false);
  });

  it("serves a database outage without crashing and keeps serving after", async () => {
    // DESIGN.md failure mode #7: Postgres down must not stop the gateway.
    let fail = true;
    const seen: number[][] = [];
    const q = new TraceQueue<number>({
      capacity: 10,
      batchSize: 2,
      flushIntervalMs: 1000,
      sink: async (b) => {
        if (fail) throw new Error("ECONNREFUSED");
        seen.push(b);
      },
    });
    q.enqueue(1);
    q.enqueue(2);
    await settle();
    expect(q.flushFailures).toBeGreaterThan(0);
    expect(q.size).toBe(2); // retained, not lost

    fail = false;
    await q.flush();
    expect(seen).toEqual([[1, 2]]);
  });

  it("bounds memory even while the sink keeps failing", async () => {
    // The scenario that makes an unbounded queue an OOM kill.
    const q = new TraceQueue<number>({
      capacity: 5,
      batchSize: 2,
      flushIntervalMs: 1000,
      sink: async () => {
        throw new Error("db down");
      },
    });
    for (let i = 0; i < 100; i++) {
      q.enqueue(i);
      await q.flush();
    }
    expect(q.size).toBeLessThanOrEqual(5);
    expect(q.dropped).toBeGreaterThan(0);
  });

  it("flushes on the interval", async () => {
    vi.useFakeTimers();
    const sink = vi.fn(async () => {});
    const q = new TraceQueue<number>({ capacity: 10, batchSize: 99, flushIntervalMs: 50, sink });
    q.start();
    q.enqueue(1);
    await vi.advanceTimersByTimeAsync(60);
    expect(sink).toHaveBeenCalledWith([1]);
    await q.stop();
    vi.useRealTimers();
  });

  it("drains on stop so a graceful shutdown does not lose buffered traces", async () => {
    const sink = vi.fn(async () => {});
    const q = new TraceQueue<number>({
      capacity: 10,
      batchSize: 99,
      flushIntervalMs: 10_000,
      sink,
    });
    q.start();
    q.enqueue(1);
    q.enqueue(2);
    await q.stop();
    expect(sink).toHaveBeenCalledWith([1, 2]);
  });

  it("refuses new rows once stopped", async () => {
    const q = new TraceQueue<number>({
      capacity: 10,
      batchSize: 99,
      flushIntervalMs: 10_000,
      sink: async () => {},
    });
    await q.stop();
    expect(q.enqueue(1)).toBe(false);
    expect(q.size).toBe(0);
  });

  it("splits a large backlog into batch-sized writes", async () => {
    const batches: number[][] = [];
    const q = new TraceQueue<number>({
      capacity: 100,
      batchSize: 3,
      flushIntervalMs: 10_000,
      sink: async (b) => {
        batches.push(b);
      },
    });
    for (let i = 1; i <= 7; i++) q.enqueue(i);
    await q.flush();
    expect(batches).toEqual([[1, 2, 3], [4, 5, 6], [7]]);
  });

  it("does not run two flushes concurrently", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    const q = new TraceQueue<number>({
      capacity: 100,
      batchSize: 1,
      flushIntervalMs: 10_000,
      sink: async () => {
        inFlight++;
        maxInFlight = Math.max(maxInFlight, inFlight);
        await new Promise((r) => setTimeout(r, 5));
        inFlight--;
      },
    });
    for (let i = 0; i < 5; i++) q.enqueue(i);
    await Promise.all([q.flush(), q.flush(), q.flush()]);
    expect(maxInFlight).toBe(1);
  });

  it("rejects nonsensical configuration", () => {
    const sink = async (): Promise<void> => {};
    expect(() => new TraceQueue({ capacity: 0, batchSize: 1, flushIntervalMs: 1, sink })).toThrow();
    expect(() => new TraceQueue({ capacity: 1, batchSize: 0, flushIntervalMs: 1, sink })).toThrow();
  });
});
