/**
 * Webhook signature verification.
 *
 * This endpoint is public: anyone can POST to it. The signature is the ONLY
 * thing establishing that a payload came from GitHub, so every rule below
 * exists to close a specific attack, not to be thorough for its own sake.
 */
import { createHmac, timingSafeEqual } from "node:crypto";

export type VerifyFailure =
  "missing_signature" | "malformed_signature" | "bad_signature" | "missing_secret";

export type VerifyResult = { ok: true } | { ok: false; reason: VerifyFailure };

/** GitHub sends `sha256=<hex>`. The prefix is part of the format, not decoration. */
const PREFIX = "sha256=";
const HEX_DIGEST = /^[0-9a-f]{64}$/;

/**
 * Verify an `X-Hub-Signature-256` header against the RAW request body.
 *
 * The body must be the exact bytes received. Verifying a re-serialised object
 * is the classic mistake here: `JSON.parse` then `JSON.stringify` can reorder
 * keys, change unicode escaping and drop insignificant whitespace, all of which
 * change the HMAC — so a valid payload fails, and the temptation is then to
 * "fix" it by weakening the check.
 */
export function verifySignature(
  rawBody: Buffer | string,
  signatureHeader: string | undefined,
  secret: string,
): VerifyResult {
  if (!secret) return { ok: false, reason: "missing_secret" };
  if (signatureHeader === undefined || signatureHeader === "") {
    return { ok: false, reason: "missing_signature" };
  }
  if (!signatureHeader.startsWith(PREFIX)) return { ok: false, reason: "malformed_signature" };

  const provided = signatureHeader.slice(PREFIX.length).toLowerCase();
  // Validate the SHAPE before comparing. timingSafeEqual throws on a length
  // mismatch, and that throw would itself be an oracle for the digest length.
  if (!HEX_DIGEST.test(provided)) return { ok: false, reason: "malformed_signature" };

  const expected = createHmac("sha256", secret)
    .update(typeof rawBody === "string" ? Buffer.from(rawBody, "utf8") : rawBody)
    .digest("hex");

  // Constant-time. A plain === leaks how many leading characters matched, and
  // an attacker who can send unlimited requests can recover the digest byte by
  // byte from the timing difference.
  const a = Buffer.from(provided, "hex");
  const b = Buffer.from(expected, "hex");
  return a.length === b.length && timingSafeEqual(a, b)
    ? { ok: true }
    : { ok: false, reason: "bad_signature" };
}

/**
 * Replay protection.
 *
 * A signature stays valid forever, so a captured-and-replayed delivery is
 * indistinguishable from a real one by signature alone. GitHub also retries
 * genuinely on failure, so the same delivery id can legitimately arrive twice
 * and must be idempotent either way.
 *
 * Bounded on purpose: an unbounded set of ids is a memory leak an attacker
 * controls the size of.
 */
export class DeliveryLog {
  readonly #seen = new Map<string, number>();

  constructor(
    private readonly capacity = 5_000,
    private readonly ttlMs = 24 * 60 * 60 * 1000,
    private readonly now: () => number = Date.now,
  ) {}

  /** True when this delivery is new. False means already processed — skip it. */
  accept(deliveryId: string): boolean {
    const t = this.now();
    this.#evict(t);
    if (this.#seen.has(deliveryId)) return false;
    this.#seen.set(deliveryId, t);
    return true;
  }

  get size(): number {
    return this.#seen.size;
  }

  #evict(now: number): void {
    for (const [id, at] of this.#seen) {
      if (now - at > this.ttlMs) this.#seen.delete(id);
    }
    // Oldest-first, because Map preserves insertion order.
    while (this.#seen.size >= this.capacity) {
      const oldest = this.#seen.keys().next().value;
      if (oldest === undefined) break;
      this.#seen.delete(oldest);
    }
  }
}

/** Does this PR touch prompts? Only those get an eval run, because runs cost money. */
export function touchesPrompts(filenames: string[], prefix = "prompts/"): boolean {
  return filenames.some((f) => f.startsWith(prefix));
}
