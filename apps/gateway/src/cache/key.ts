/**
 * Cache identity and invalidation.
 *
 * Two independent caches share these rules:
 *   - the EXACT cache (Redis), keyed on a hash of the canonicalised request
 *   - the SEMANTIC cache (pgvector), keyed on an embedding of the prompt
 *
 * INVALIDATION. A cached answer is only valid for the prompt that produced it.
 * Change the system prompt and every stored answer is potentially wrong, and
 * nothing about the stored row reveals that — the response still looks like a
 * perfectly good response. So the prompt version is part of the KEY rather than
 * a field to be checked: a bump makes old entries unreachable instead of stale,
 * which fails closed (DECISIONS.md D-048).
 *
 * The model is in the key for the same reason. Two models answering the same
 * question give different answers, and serving one for the other is a false hit
 * dressed up as a cache design.
 */
import { createHash } from "node:crypto";

/** Fields that change the answer. Anything absent here is deliberately ignored. */
export interface CacheScope {
  model: string;
  promptVersion: string;
}

/**
 * Canonicalise a request so that trivia cannot cause a miss.
 *
 * Key ordering and whitespace do not change what was asked, so they must not
 * change the key — otherwise two identical requests from different clients
 * miss each other and the cache quietly does nothing.
 */
export function canonicalise(messages: Array<{ role: string; content: string }>): string {
  return messages
    .map((m) => `${m.role.trim().toLowerCase()}:${m.content.trim().replace(/\s+/g, " ")}`)
    .join("\n");
}

export function exactKey(
  messages: Array<{ role: string; content: string }>,
  scope: CacheScope,
  params: Record<string, unknown>,
): string {
  const payload = JSON.stringify({
    messages: canonicalise(messages),
    model: scope.model,
    promptVersion: scope.promptVersion,
    // Sampling parameters change the answer, so they change the key.
    params: Object.fromEntries(Object.entries(params).sort(([a], [b]) => a.localeCompare(b))),
  });
  return `verdict:exact:${createHash("sha256").update(payload).digest("hex")}`;
}

/**
 * The text that gets embedded for near-duplicate search.
 *
 * Only the USER turns. A shared system prompt is identical across every request
 * in a route, so including it drags every similarity toward 1.0 and destroys
 * the discrimination the threshold depends on. The system prompt is already
 * accounted for by `promptVersion`.
 */
export function embeddingText(messages: Array<{ role: string; content: string }>): string {
  const user = messages.filter((m) => m.role === "user" || m.role === "assistant");
  return canonicalise(user.length > 0 ? user : messages);
}

/** Seconds until a cache entry expires. */
export interface TtlPolicy {
  defaultSeconds: number;
  /** Per-route overrides; volatile routes should expire sooner. */
  byRoute?: Record<string, number>;
}

export function ttlSecondsFor(policy: TtlPolicy, routeKey: string | null): number {
  if (routeKey !== null && policy.byRoute?.[routeKey] !== undefined) {
    return policy.byRoute[routeKey];
  }
  return policy.defaultSeconds;
}

export function expiryFrom(now: Date, seconds: number): Date {
  return new Date(now.getTime() + seconds * 1000);
}
