/**
 * Bounded write-behind queue for trace persistence.
 *
 * DECISIONS.md D-007: trace recording must not be on the request path. A
 * database round-trip inside a proxy whose whole justification is that it is
 * ~99% network wait would be self-defeating, and it would make Postgres
 * availability a hard dependency of serving availability.
 *
 * When the sink cannot keep up or the database is down, the queue fills and the
 * OLDEST rows are dropped, with a counter. Verdict prefers losing observability
 * to losing availability — and the only way that is a defensible position
 * rather than an accident is if the loss is bounded, deliberate and counted.
 *
 * An unbounded queue was rejected outright: it converts a database outage into
 * an OOM kill, which turns a degradation into an incident.
 */

export interface TraceQueueOptions<T> {
  capacity: number;
  batchSize: number;
  flushIntervalMs: number;
  sink: (rows: T[]) => Promise<void>;
  onDrop?: (count: number) => void;
  onError?: (err: unknown, rows: number) => void;
}

export class TraceQueue<T> {
  readonly #opts: TraceQueueOptions<T>;
  #buffer: T[] = [];
  #dropped = 0;
  #flushFailures = 0;
  #timer: NodeJS.Timeout | undefined;
  #inFlight: Promise<void> | undefined;
  #stopped = false;

  constructor(opts: TraceQueueOptions<T>) {
    if (opts.capacity < 1) throw new Error("capacity must be >= 1");
    if (opts.batchSize < 1) throw new Error("batchSize must be >= 1");
    this.#opts = opts;
  }

  get size(): number {
    return this.#buffer.length;
  }

  /** Rows discarded because the queue was full. Exposed as a metric. */
  get dropped(): number {
    return this.#dropped;
  }

  get flushFailures(): number {
    return this.#flushFailures;
  }

  /** Never throws and never blocks. Returns false when the row was dropped. */
  enqueue(row: T): boolean {
    if (this.#stopped) return false;

    if (this.#buffer.length >= this.#opts.capacity) {
      // Drop the OLDEST. Under sustained overload the recent past is more
      // useful for debugging than the distant past.
      this.#buffer.shift();
      this.#dropped += 1;
      this.#opts.onDrop?.(this.#dropped);
      this.#buffer.push(row);
      return false;
    }

    this.#buffer.push(row);
    if (this.#buffer.length >= this.#opts.batchSize) void this.flush();
    return true;
  }

  start(): void {
    if (this.#timer !== undefined) return;
    this.#stopped = false;
    this.#timer = setInterval(() => void this.flush(), this.#opts.flushIntervalMs);
    // Never hold the process open just to flush analytics.
    this.#timer.unref?.();
  }

  /** Stop accepting rows and drain whatever is buffered. */
  async stop(): Promise<void> {
    this.#stopped = true;
    if (this.#timer !== undefined) {
      clearInterval(this.#timer);
      this.#timer = undefined;
    }
    await this.flush();
  }

  /**
   * Drain the buffer. Awaiting this must mean "everything queued when I called
   * is written" — otherwise `stop()` can return while a flush is still in
   * flight and a graceful shutdown silently loses traces. Concurrent callers
   * therefore wait on the in-flight drain rather than returning early.
   */
  async flush(): Promise<void> {
    while (this.#inFlight !== undefined) await this.#inFlight;
    if (this.#buffer.length === 0) return;

    const run = this.#drain();
    this.#inFlight = run;
    try {
      await run;
    } finally {
      this.#inFlight = undefined;
    }
  }

  async #drain(): Promise<void> {
    try {
      while (this.#buffer.length > 0) {
        const batch = this.#buffer.splice(0, this.#opts.batchSize);
        try {
          await this.#opts.sink(batch);
        } catch (err) {
          this.#flushFailures += 1;
          this.#opts.onError?.(err, batch.length);

          // Put the batch back at the FRONT so a transient blip does not lose
          // it, but only as far as capacity allows — the bound is what stops a
          // database outage becoming unbounded memory growth.
          const room = this.#opts.capacity - this.#buffer.length;
          if (room > 0) {
            const keep = batch.slice(-room);
            this.#dropped += batch.length - keep.length;
            this.#buffer.unshift(...keep);
          } else {
            this.#dropped += batch.length;
          }
          this.#opts.onDrop?.(this.#dropped);
          return; // stop trying this cycle; the interval will retry
        }
      }
    } catch {
      // #drain's inner try already records sink failures; this guard only
      // stops an unexpected throw from leaving #inFlight set forever.
    }
  }
}
