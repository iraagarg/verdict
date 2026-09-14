/**
 * OpenAI adapter, which also serves Groq.
 *
 * Groq exposes an OpenAI-compatible Chat Completions endpoint, so one client
 * class covers both; only `baseURL`, the key and the provider label differ.
 * That is the whole reason this file is not duplicated.
 *
 * Usage timing differs from Anthropic and the difference is real: these
 * providers report token counts once, in a final chunk, not incrementally.
 * The meter is told the moment that chunk arrives and never estimates in the
 * meantime (DECISIONS.md D-017).
 */
import OpenAI from "openai";
import { z } from "zod";
import type { FinishReason, ModelEntry } from "@verdict/shared";
import {
  ProviderError,
  type NormalizedRequest,
  type ProviderAdapter,
  type ProviderEvent,
  type ProviderFailure,
  type ProviderName,
  type StreamContext,
} from "./types.js";

function mapFinishReason(reason: string | null | undefined): FinishReason {
  switch (reason) {
    case "length":
      return "length";
    case "content_filter":
      return "content_filter";
    case "tool_calls":
    case "function_call":
      return "tool_calls";
    case "stop":
    default:
      return "stop";
  }
}

/**
 * Groq returns usage under a vendor extension rather than the standard `usage`
 * field. Parsed with Zod instead of an `any` cast so an unexpected shape is a
 * miss, not a crash.
 */
const GroqExtra = z.object({
  x_groq: z
    .object({
      usage: z
        .object({
          prompt_tokens: z.number().int().nonnegative().optional(),
          completion_tokens: z.number().int().nonnegative().optional(),
        })
        .optional(),
    })
    .optional(),
});

export function classifyOpenAIError(err: unknown): ProviderFailure {
  if (err instanceof OpenAI.APIConnectionTimeoutError) {
    return {
      kind: "timeout",
      status: undefined,
      retryable: true,

      countsTowardBreaker: true,
      retryAfterMs: undefined,
      message: err.message,
    };
  }
  if (err instanceof OpenAI.APIConnectionError) {
    return {
      kind: "connection",
      status: undefined,
      retryable: true,

      countsTowardBreaker: true,
      retryAfterMs: undefined,
      message: err.message,
    };
  }
  if (err instanceof OpenAI.RateLimitError) {
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
  if (err instanceof OpenAI.AuthenticationError || err instanceof OpenAI.PermissionDeniedError) {
    return {
      kind: "auth",
      status: err.status,
      retryable: false,

      countsTowardBreaker: true,
      retryAfterMs: undefined,
      message: err.message,
    };
  }
  if (err instanceof OpenAI.APIError) {
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

export class OpenAICompatibleAdapter implements ProviderAdapter {
  readonly name: ProviderName;
  readonly #client: OpenAI;

  constructor(name: ProviderName, apiKey: string, baseURL?: string) {
    this.name = name;
    this.#client = new OpenAI({
      apiKey,
      ...(baseURL === undefined ? {} : { baseURL }),
      maxRetries: 0,
    });
  }

  async *streamChat(
    req: NormalizedRequest,
    _model: ModelEntry,
    ctx: StreamContext,
  ): AsyncIterable<ProviderEvent> {
    const messages: OpenAI.Chat.ChatCompletionMessageParam[] = [];
    if (req.system !== undefined) messages.push({ role: "system", content: req.system });
    for (const m of req.messages) messages.push({ role: m.role, content: m.content });

    let stream;
    try {
      stream = await this.#client.chat.completions.create(
        {
          model: req.model,
          messages,
          max_completion_tokens: req.maxOutputTokens,
          ...(req.temperature === undefined ? {} : { temperature: req.temperature }),
          ...(req.topP === undefined ? {} : { top_p: req.topP }),
          ...(req.stop === undefined ? {} : { stop: req.stop }),
          stream: true,
          // Without this the stream carries no token counts at all and every
          // cost would be unknown.
          stream_options: { include_usage: true },
        },
        { signal: ctx.signal, timeout: ctx.timeoutMs },
      );
    } catch (err) {
      throw new ProviderError(this.name, classifyOpenAIError(err), { cause: err });
    }

    let started = false;
    let finishReason: FinishReason = "stop";

    try {
      for await (const chunk of stream) {
        if (!started) {
          started = true;
          yield { type: "start", providerMessageId: chunk.id, model: chunk.model };
        }

        const choice = chunk.choices[0];
        if (choice?.delta?.content !== undefined && choice.delta.content !== null) {
          yield { type: "text", delta: choice.delta.content };
        }
        if (choice?.finish_reason !== undefined && choice.finish_reason !== null) {
          finishReason = mapFinishReason(choice.finish_reason);
        }

        if (chunk.usage) {
          yield {
            type: "usage",
            usage: {
              inputTokens: chunk.usage.prompt_tokens,
              outputTokens: chunk.usage.completion_tokens,
            },
          };
        } else {
          const extra = GroqExtra.safeParse(chunk);
          const groqUsage = extra.success ? extra.data.x_groq?.usage : undefined;
          if (groqUsage) {
            yield {
              type: "usage",
              usage: {
                ...(groqUsage.prompt_tokens === undefined
                  ? {}
                  : { inputTokens: groqUsage.prompt_tokens }),
                ...(groqUsage.completion_tokens === undefined
                  ? {}
                  : { outputTokens: groqUsage.completion_tokens }),
              },
            };
          }
        }
      }
    } catch (err) {
      if (ctx.signal.aborted) return;
      throw new ProviderError(this.name, classifyOpenAIError(err), { cause: err });
    }

    yield { type: "finish", finishReason };
  }
}
