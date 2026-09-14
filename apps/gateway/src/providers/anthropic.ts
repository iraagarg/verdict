/**
 * Anthropic adapter.
 *
 * Uses the official SDK's streaming iterator, so we consume raw
 * `RawMessageStreamEvent`s as they arrive rather than letting a helper
 * accumulate a full message. `maxRetries: 0` because the gateway owns retry,
 * backoff and the breaker — two retry policies compose into an unpredictable
 * one, and the breaker would never see the failures it exists to count
 * (DECISIONS.md D-013).
 */
import Anthropic from "@anthropic-ai/sdk";
import type { FinishReason, ModelEntry } from "@verdict/shared";
import {
  ProviderError,
  type NormalizedRequest,
  type ProviderAdapter,
  type ProviderEvent,
  type ProviderFailure,
  type StreamContext,
} from "./types.js";

/**
 * `refusal` is a policy outcome, not a quality outcome, and it arrives as a
 * successful HTTP 200 — code that only checks status treats it as an empty
 * success. DESIGN.md §6.3.
 */
function mapStopReason(reason: string | null | undefined): FinishReason {
  switch (reason) {
    case "max_tokens":
      return "length";
    case "refusal":
      return "content_filter";
    case "tool_use":
      return "tool_calls";
    case "end_turn":
    case "stop_sequence":
    default:
      return "stop";
  }
}

export function classifyAnthropicError(err: unknown): ProviderFailure {
  if (err instanceof Anthropic.APIConnectionTimeoutError) {
    return {
      kind: "timeout",
      status: undefined,
      retryable: true,

      countsTowardBreaker: true,
      retryAfterMs: undefined,
      message: err.message,
    };
  }
  if (err instanceof Anthropic.APIConnectionError) {
    return {
      kind: "connection",
      status: undefined,
      retryable: true,

      countsTowardBreaker: true,
      retryAfterMs: undefined,
      message: err.message,
    };
  }
  if (err instanceof Anthropic.RateLimitError) {
    const header = err.headers?.get?.("retry-after");
    const seconds = header === null || header === undefined ? Number.NaN : Number(header);
    return {
      kind: "rate_limited",
      status: 429,
      retryable: true,
      // Back-pressure, not ill health. Must not open the breaker.
      countsTowardBreaker: false,
      retryAfterMs: Number.isFinite(seconds) ? seconds * 1000 : undefined,
      message: err.message,
    };
  }
  if (
    err instanceof Anthropic.AuthenticationError ||
    err instanceof Anthropic.PermissionDeniedError
  ) {
    return {
      kind: "auth",
      status: err.status,
      retryable: false,

      countsTowardBreaker: true,
      retryAfterMs: undefined,
      message: err.message,
    };
  }
  if (err instanceof Anthropic.APIError) {
    const status = err.status ?? 0;
    const is5xx = status >= 500;
    return {
      kind: is5xx ? "provider_5xx" : "bad_request",
      status: err.status,
      retryable: is5xx,
      // A 400 is our bug, not the provider's; it says nothing about health.
      countsTowardBreaker: is5xx,
      retryAfterMs: undefined,
      message: err.message,
    };
  }
  return {
    kind: "unknown",
    status: undefined,
    retryable: false,
    countsTowardBreaker: true,
    retryAfterMs: undefined,
    message: err instanceof Error ? err.message : String(err),
  };
}

export class AnthropicAdapter implements ProviderAdapter {
  readonly name = "anthropic" as const;
  readonly #client: Anthropic;

  constructor(apiKey: string, baseURL?: string) {
    this.#client = new Anthropic({
      apiKey,
      ...(baseURL === undefined ? {} : { baseURL }),
      maxRetries: 0,
    });
  }

  async *streamChat(
    req: NormalizedRequest,
    model: ModelEntry,
    ctx: StreamContext,
  ): AsyncIterable<ProviderEvent> {
    let stream;
    try {
      stream = await this.#client.messages.create(
        {
          model: req.model,
          max_tokens: req.maxOutputTokens,
          ...(req.system === undefined ? {} : { system: req.system }),
          messages: req.messages,
          ...(req.temperature === undefined ? {} : { temperature: req.temperature }),
          ...(req.topP === undefined ? {} : { top_p: req.topP }),
          ...(req.stop === undefined ? {} : { stop_sequences: req.stop }),
          stream: true,
        },
        { signal: ctx.signal, timeout: ctx.timeoutMs },
      );
    } catch (err) {
      throw new ProviderError("anthropic", classifyAnthropicError(err), { cause: err });
    }

    let finishReason: FinishReason = "stop";
    try {
      for await (const event of stream) {
        switch (event.type) {
          case "message_start": {
            const u = event.message.usage;
            yield {
              type: "start",
              providerMessageId: event.message.id,
              model: event.message.model,
              usage: {
                inputTokens: u.input_tokens,
                outputTokens: u.output_tokens,
                cacheReadTokens: u.cache_read_input_tokens ?? 0,
                cacheWriteTokens: u.cache_creation_input_tokens ?? 0,
              },
            };
            break;
          }
          case "content_block_delta": {
            if (event.delta.type === "text_delta") {
              yield { type: "text", delta: event.delta.text };
            } else if (event.delta.type === "thinking_delta") {
              yield { type: "thinking", delta: event.delta.thinking };
            }
            break;
          }
          case "message_delta": {
            finishReason = mapStopReason(event.delta.stop_reason);
            // Cumulative output count — the meter treats usage as absolute.
            yield { type: "usage", usage: { outputTokens: event.usage.output_tokens } };
            break;
          }
          case "message_stop": {
            yield { type: "finish", finishReason };
            break;
          }
          default:
            break;
        }
      }
    } catch (err) {
      if (ctx.signal.aborted) return; // client hung up; stop cleanly
      throw new ProviderError("anthropic", classifyAnthropicError(err), { cause: err });
    }
  }
}
