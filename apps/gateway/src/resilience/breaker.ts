/**
 * Per-provider circuit breaker.
 *
 * Written rather than imported so that every transition is testable and
 * defensible. The state machine is deliberately small:
 *
 *   closed    → normal. Consecutive failures are counted; reaching the
 *               threshold opens the breaker.
 *   open      → every attempt is refused without touching the provider. After
 *               `cooldownMs` the next `canAttempt()` moves to half-open.
 *   half-open → exactly ONE probe is allowed through. Success closes the
 *               breaker and resets the count; failure re-opens it and restarts
 *               the cooldown.
 *
 * The single-probe rule matters: without it, the moment the cooldown expires
 * every queued request stampedes a provider that is probably still unhealthy.
 *
 * The clock is injected so tests advance time instead of sleeping.
 */

export type BreakerState = "closed" | "open" | "half-open";

export interface BreakerOptions {
  /** Consecutive failures in `closed` that trip the breaker. */
  failureThreshold: number;
  /** How long `open` lasts before a probe is permitted. */
  cooldownMs: number;
  /** Injected for tests; defaults to wall clock. */
  now?: () => number;
}

export class CircuitOpenError extends Error {
  override readonly name = "CircuitOpenError";
  constructor(
    readonly provider: string,
    readonly retryAfterMs: number,
  ) {
    super(`circuit breaker is open for provider "${provider}"; retry in ${retryAfterMs}ms`);
  }
}

export class CircuitBreaker {
  readonly #name: string;
  readonly #threshold: number;
  readonly #cooldownMs: number;
  readonly #now: () => number;

  #state: BreakerState = "closed";
  #consecutiveFailures = 0;
  #openedAt = 0;
  #probeInFlight = false;

  constructor(name: string, opts: BreakerOptions) {
    if (opts.failureThreshold < 1) throw new Error("failureThreshold must be >= 1");
    if (opts.cooldownMs < 0) throw new Error("cooldownMs must be >= 0");
    this.#name = name;
    this.#threshold = opts.failureThreshold;
    this.#cooldownMs = opts.cooldownMs;
    this.#now = opts.now ?? Date.now;
  }

  get name(): string {
    return this.#name;
  }

  /**
   * Current state, after applying any cooldown expiry. Reading state can move
   * `open` → `half-open`; that is intentional and keeps the transition in one
   * place rather than duplicated between the getter and `canAttempt`.
   */
  get state(): BreakerState {
    if (this.#state === "open" && this.#now() - this.#openedAt >= this.#cooldownMs) {
      this.#state = "half-open";
      this.#probeInFlight = false;
    }
    return this.#state;
  }

  get consecutiveFailures(): number {
    return this.#consecutiveFailures;
  }

  /** Milliseconds until a probe is allowed. Zero unless currently open. */
  retryAfterMs(): number {
    if (this.state !== "open") return 0;
    return Math.max(0, this.#cooldownMs - (this.#now() - this.#openedAt));
  }

  /**
   * Reserve an attempt. Returns false when the call must not be made.
   * In half-open this reserves the single probe slot.
   */
  canAttempt(): boolean {
    const state = this.state;
    if (state === "closed") return true;
    if (state === "open") return false;
    if (this.#probeInFlight) return false;
    this.#probeInFlight = true;
    return true;
  }

  /** Throwing variant, for call sites that want the error shape. */
  assertCanAttempt(): void {
    if (!this.canAttempt()) throw new CircuitOpenError(this.#name, this.retryAfterMs());
  }

  onSuccess(): void {
    this.#consecutiveFailures = 0;
    this.#probeInFlight = false;
    this.#state = "closed";
  }

  onFailure(): void {
    this.#probeInFlight = false;

    // A failed probe re-opens immediately and restarts the cooldown; it does
    // not need to re-accumulate `failureThreshold` failures to do so.
    if (this.#state === "half-open") {
      this.#trip();
      return;
    }

    this.#consecutiveFailures += 1;
    if (this.#consecutiveFailures >= this.#threshold) this.#trip();
  }

  /** Force closed. Used on config reload and in tests. */
  reset(): void {
    this.#state = "closed";
    this.#consecutiveFailures = 0;
    this.#probeInFlight = false;
    this.#openedAt = 0;
  }

  #trip(): void {
    this.#state = "open";
    this.#openedAt = this.#now();
    this.#consecutiveFailures = this.#threshold;
  }
}
