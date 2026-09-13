/**
 * Turn a validated OpenAI request into a provider-agnostic request for a
 * specific model, dropping parameters that model rejects.
 *
 * This is where DECISIONS.md D-010 lives. The ladder is not parameter-uniform:
 * `temperature` and `top_p` return HTTP 400 on Claude Opus 5 and Sonnet 5 but
 * are accepted by Claude Haiku 4.5, OpenAI and Groq. Since Verdict's whole
 * premise is substituting one rung for another underneath an unchanged client
 * request, that incompatibility is ours to absorb — but never silently.
 */
import { messageText, type ChatCompletionRequest, type ModelEntry } from "@verdict/shared";
import type { NormalizedRequest } from "./types.js";

export interface NormalizeResult {
  request: NormalizedRequest;
  /** Parameters removed because the target model rejects them. Reported to the client. */
  droppedParams: string[];
}

export class IncompatibleParameterError extends Error {
  override readonly name = "IncompatibleParameterError";
  constructor(
    readonly param: string,
    readonly model: string,
  ) {
    super(
      `model "${model}" does not accept "${param}". Either remove it, or use model "verdict-auto" ` +
        `and let Verdict pick a model that does.`,
    );
  }
}

export interface NormalizeOptions {
  /** True when the caller pinned this exact model, false when Verdict chose it. */
  modelWasPinned: boolean;
  /** Cap applied when the client supplies no max_tokens. */
  defaultMaxOutputTokens: number;
}

export function normalizeRequest(
  body: ChatCompletionRequest,
  modelId: string,
  model: ModelEntry,
  opts: NormalizeOptions,
): NormalizeResult {
  // OpenAI treats `developer` as a higher-priority `system`; both map to the
  // same place for every provider we support.
  const systemParts = body.messages
    .filter((m) => m.role === "system" || m.role === "developer")
    .map((m) => messageText(m.content));

  const messages = body.messages
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => ({
      role: m.role as "user" | "assistant",
      content: messageText(m.content),
    }));

  const requested = body.max_completion_tokens ?? body.max_tokens;
  const maxOutputTokens = Math.min(
    requested ?? opts.defaultMaxOutputTokens,
    model.max_output_tokens,
  );

  const droppedParams: string[] = [];
  let temperature = body.temperature;
  let topP = body.top_p;

  if (!model.capabilities.supports_sampling_params) {
    for (const [name, value] of [
      ["temperature", temperature],
      ["top_p", topP],
    ] as const) {
      if (value === undefined) continue;
      // If the caller chose this model themselves, the incompatibility is
      // theirs to resolve and a silent drop would mislead them. If Verdict
      // routed them here, Verdict owns the problem and must not fail the
      // request — but must say what it did.
      if (opts.modelWasPinned) throw new IncompatibleParameterError(name, modelId);
      droppedParams.push(name);
    }
    temperature = undefined;
    topP = undefined;
  }

  return {
    request: {
      model: modelId,
      system: systemParts.length > 0 ? systemParts.join("\n\n") : undefined,
      messages,
      maxOutputTokens,
      temperature,
      topP,
      stop:
        body.stop === undefined
          ? undefined
          : typeof body.stop === "string"
            ? [body.stop]
            : body.stop,
    },
    droppedParams,
  };
}
