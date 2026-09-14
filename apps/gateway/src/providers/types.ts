/**
 * The one interface every provider hides behind.
 *
 * Adapters translate a provider's native streaming protocol into this normalised
 * event sequence. Nothing above this layer knows that Anthropic streams
 * `content_block_delta` while OpenAI streams `chat.completion.chunk`, or that
 * Claude Opus 5 rejects `temperature` while Claude Haiku 4.5 accepts it.
 *
 * Events are yielded as they arrive. No adapter may buffer a full response.
 */
import type { FinishReason, ModelEntry } from "@verdict/shared";
import type { TokenUsage } from "../cost/meter.js";

export const PROVIDERS = ["anthropic", "openai", "groq"] as const;
export type ProviderName = (typeof PROVIDERS)[number];

/** A provider-agnostic request, already normalised for the target model. */
export interface NormalizedRequest {
  /** The provider's own model id. */
  model: string;
  system: string | undefined;
  messages: Array<{ role: "user" | "assistant"; content: string }>;
  maxOutputTokens: number;
  temperature: number | undefined;
  topP: number | undefined;
  stop: string[] | undefined;
}

export type ProviderEvent =
  /** First event. Carries the provider's message id and any usage known up front. */
  | { type: "start"; providerMessageId: string; model: string; usage?: Partial<TokenUsage> }
  /** A chunk of visible assistant text. */
  | { type: "text"; delta: string }
  /**
   * Reasoning text. Recorded on the trace but NEVER forwarded to the client —
   * no OpenAI-compatible client expects a thinking block. Its tokens are still
   * billed and still counted, because they appear in the provider's output
   * token total. DESIGN.md §6.3.
   */
  | { type: "thinking"; delta: string }
  /** An absolute usage observation. */
  | { type: "usage"; usage: Partial<TokenUsage> }
  /** Terminal event. */
  | { type: "finish"; finishReason: FinishReason; usage?: Partial<TokenUsage> };

export interface StreamContext {
  signal: AbortSignal;
  /** Per-request deadline for the whole upstream call. */
  timeoutMs: number;
  requestId: string;
}

export interface ProviderAdapter {
  readonly name: ProviderName;
  /**
   * Open an upstream stream and yield normalised events.
   * Must honour `ctx.signal` and stop consuming upstream tokens when it aborts.
   */
  streamChat(
    req: NormalizedRequest,
    model: ModelEntry,
    ctx: StreamContext,
  ): AsyncIterable<ProviderEvent>;
}

/** Classification used by the retry policy and the circuit breaker. */
export interface ProviderFailure {
  kind:
    "provider_5xx" | "rate_limited" | "timeout" | "auth" | "bad_request" | "connection" | "unknown";
  status: number | undefined;
  retryable: boolean;
  /**
   * Whether this failure is evidence the provider is UNHEALTHY.
   *
   * A circuit breaker exists to stop calling a broken service. HTTP 429 does
   * not mean broken — it means healthy and asking you to slow down. Counting
   * back-pressure as failure conflates "the provider is down" with "you are
   * going too fast", and the correct responses to those are opposite: one is
   * stop, the other is pace yourself.
   */
  countsTowardBreaker: boolean;
  retryAfterMs: number | undefined;
  message: string;
}

export class ProviderError extends Error {
  override readonly name = "ProviderError";
  constructor(
    readonly provider: ProviderName,
    readonly failure: ProviderFailure,
    options?: { cause?: unknown },
  ) {
    super(`${provider}: ${failure.message}`, options);
  }
}
