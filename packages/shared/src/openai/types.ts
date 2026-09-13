/**
 * The OpenAI-compatible wire contract.
 *
 * Verdict's promise is that a user changes `baseURL` and nothing else, so these
 * schemas are the contract boundary in both directions: they validate what
 * arrives and they shape what we emit. DESIGN.md §6.
 *
 * The request schema is `.strict()` on purpose. Silently ignoring an unknown
 * field means a user who sends `tools: [...]` gets a plain completion back and
 * spends real money discovering that we did not support it. A 400 naming the
 * field is cheaper for everyone.
 */
import { z } from "zod";

/** A single content part. v1 accepts text only; images arrive with the vision work. */
const TextPart = z
  .object({
    type: z.literal("text"),
    text: z.string(),
  })
  .strict();

const MessageContent = z.union([z.string(), z.array(TextPart).min(1)]);

export const ChatMessage = z
  .object({
    role: z.enum(["system", "developer", "user", "assistant"]),
    content: MessageContent,
    name: z.string().optional(),
  })
  .strict();

export type ChatMessage = z.infer<typeof ChatMessage>;

export const ChatCompletionRequest = z
  .object({
    /** A ladder rung id, or the sentinel `verdict-auto` to delegate to the router. */
    model: z.string().min(1),
    messages: z.array(ChatMessage).min(1),
    stream: z.boolean().default(false),
    stream_options: z
      .object({ include_usage: z.boolean().default(false) })
      .strict()
      .optional(),
    max_tokens: z.number().int().positive().optional(),
    max_completion_tokens: z.number().int().positive().optional(),
    temperature: z.number().min(0).max(2).optional(),
    top_p: z.number().gt(0).max(1).optional(),
    stop: z.union([z.string(), z.array(z.string()).max(4)]).optional(),
    /** Recorded on the trace for per-user attribution. */
    user: z.string().optional(),
    /** Accepted and echoed; `n > 1` is rejected below. */
    n: z.number().int().optional(),
  })
  .strict()
  .superRefine((req, ctx) => {
    // v1 is single-turn and non-agentic (DESIGN.md §3 non-goals). Rejecting
    // n > 1 explicitly beats returning one choice and letting the caller wonder.
    if (req.n !== undefined && req.n !== 1) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["n"],
        message: "n must be 1; Verdict does not support multiple choices per request",
      });
    }
    if (req.messages.every((m) => m.role === "system" || m.role === "developer")) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["messages"],
        message: "at least one non-system message is required",
      });
    }
  });

export type ChatCompletionRequest = z.infer<typeof ChatCompletionRequest>;

export const FINISH_REASONS = ["stop", "length", "content_filter", "tool_calls"] as const;
export type FinishReason = (typeof FINISH_REASONS)[number];

export interface CompletionUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface ChatCompletionChunk {
  id: string;
  object: "chat.completion.chunk";
  created: number;
  model: string;
  choices: Array<{
    index: 0;
    delta: { role?: "assistant"; content?: string };
    finish_reason: FinishReason | null;
  }>;
  usage?: CompletionUsage | null;
}

export interface ChatCompletion {
  id: string;
  object: "chat.completion";
  created: number;
  model: string;
  choices: Array<{
    index: 0;
    message: { role: "assistant"; content: string };
    finish_reason: FinishReason;
  }>;
  usage: CompletionUsage;
}

/** OpenAI-shaped error body. Clients parse this, so the shape is load-bearing. */
export interface OpenAIErrorBody {
  error: {
    message: string;
    type: "invalid_request_error" | "server_error" | "rate_limit_error" | "authentication_error";
    param: string | null;
    code: string | null;
  };
}

export function errorBody(
  message: string,
  type: OpenAIErrorBody["error"]["type"],
  code: string | null,
  param: string | null = null,
): OpenAIErrorBody {
  return { error: { message, type, param, code } };
}

/** Flatten string-or-parts content into plain text. */
export function messageText(content: ChatMessage["content"]): string {
  return typeof content === "string" ? content : content.map((p) => p.text).join("");
}

/** Serialize one SSE frame. Every emitted frame goes through this function. */
export function sseFrame(payload: unknown): string {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

/** The terminating frame of an OpenAI stream. */
export const SSE_DONE = "data: [DONE]\n\n";
