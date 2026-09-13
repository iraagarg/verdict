/**
 * Retry with exponential backoff and full jitter.
 *
 * "Full jitter" means the delay is uniform in [0, backoff] rather than
 * backoff ± noise. It is the variant that actually decorrelates a thundering
 * herd: capped exponential alone has every client retrying at the same instant
 * after a shared outage.
 *
 * Both the delay source and the sleep are injectable so tests are deterministic
 * and instant.
 */

export interface RetryOptions {
  /** Number of RETRIES after the initial attempt. 2 means up to 3 calls. */
  retries: number;
  baseDelayMs: number;
  maxDelayMs: number;
  /** Decides whether a given error is worth retrying. */
  isRetryable: (err: unknown) => boolean;
  /** Provider-supplied Retry-After, in ms, if the error carries one. */
  retryAfterMs?: (err: unknown) => number | undefined;
  signal?: AbortSignal;
  random?: () => number;
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
  onRetry?: (info: { attempt: number; delayMs: number; err: unknown }) => void;
}

export class AbortedError extends Error {
  override readonly name = "AbortedError";
  constructor() {
    super("operation aborted");
  }
}

/** Uniform in [0, min(maxDelayMs, baseDelayMs * 2^attempt)]. `attempt` is 0-based. */
export function backoffDelay(
  attempt: number,
  baseDelayMs: number,
  maxDelayMs: number,
  random: () => number = Math.random,
): number {
  const ceiling = Math.min(maxDelayMs, baseDelayMs * 2 ** attempt);
  return Math.floor(random() * ceiling);
}

function defaultSleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new AbortedError());
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = (): void => {
      clearTimeout(timer);
      reject(new AbortedError());
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export async function withRetry<T>(
  fn: (attempt: number) => Promise<T>,
  opts: RetryOptions,
): Promise<T> {
  const random = opts.random ?? Math.random;
  const sleep = opts.sleep ?? defaultSleep;
  let lastError: unknown;

  for (let attempt = 0; attempt <= opts.retries; attempt++) {
    if (opts.signal?.aborted) throw new AbortedError();
    try {
      return await fn(attempt);
    } catch (err) {
      lastError = err;
      if (err instanceof AbortedError || opts.signal?.aborted) throw err;
      if (attempt === opts.retries || !opts.isRetryable(err)) throw err;

      // A provider that tells us when to come back knows better than our curve,
      // but we still jitter it so a shared Retry-After does not re-synchronise
      // every client onto the same millisecond.
      const advertised = opts.retryAfterMs?.(err);
      const delayMs =
        advertised === undefined
          ? backoffDelay(attempt, opts.baseDelayMs, opts.maxDelayMs, random)
          : Math.min(opts.maxDelayMs, advertised) +
            backoffDelay(0, opts.baseDelayMs, opts.baseDelayMs, random);

      opts.onRetry?.({ attempt, delayMs, err });
      await sleep(delayMs, opts.signal);
    }
  }
  throw lastError;
}
