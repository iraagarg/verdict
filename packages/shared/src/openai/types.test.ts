import { describe, expect, it } from "vitest";
import {
  ChatCompletionRequest,
  errorBody,
  messageText,
  SSE_DONE,
  sseFrame,
  type ChatCompletionChunk,
} from "./types.js";

const MINIMAL = { model: "claude-haiku-4-5", messages: [{ role: "user", content: "hi" }] };

describe("ChatCompletionRequest", () => {
  it("accepts a minimal OpenAI request and defaults stream to false", () => {
    const parsed = ChatCompletionRequest.parse(MINIMAL);
    expect(parsed.stream).toBe(false);
    expect(parsed.model).toBe("claude-haiku-4-5");
  });

  it("accepts content parts as well as a plain string", () => {
    const parsed = ChatCompletionRequest.parse({
      ...MINIMAL,
      messages: [{ role: "user", content: [{ type: "text", text: "hello" }] }],
    });
    expect(messageText(parsed.messages[0]!.content)).toBe("hello");
  });

  it("rejects an unknown field instead of silently ignoring it", () => {
    // A user sending `tools` must learn we don't support them, not pay for a
    // plain completion and work it out afterwards.
    const res = ChatCompletionRequest.safeParse({ ...MINIMAL, tools: [] });
    expect(res.success).toBe(false);
  });

  it("rejects n > 1", () => {
    const res = ChatCompletionRequest.safeParse({ ...MINIMAL, n: 3 });
    expect(res.success).toBe(false);
    if (!res.success) expect(res.error.issues[0]?.message).toMatch(/n must be 1/);
  });

  it("accepts n = 1", () => {
    expect(ChatCompletionRequest.safeParse({ ...MINIMAL, n: 1 }).success).toBe(true);
  });

  it("requires at least one message", () => {
    expect(ChatCompletionRequest.safeParse({ ...MINIMAL, messages: [] }).success).toBe(false);
  });

  it("rejects a request that is nothing but system messages", () => {
    const res = ChatCompletionRequest.safeParse({
      ...MINIMAL,
      messages: [{ role: "system", content: "be helpful" }],
    });
    expect(res.success).toBe(false);
  });

  it("rejects out-of-range sampling parameters", () => {
    expect(ChatCompletionRequest.safeParse({ ...MINIMAL, temperature: 5 }).success).toBe(false);
    expect(ChatCompletionRequest.safeParse({ ...MINIMAL, top_p: 0 }).success).toBe(false);
    expect(ChatCompletionRequest.safeParse({ ...MINIMAL, top_p: 1 }).success).toBe(true);
  });

  it("accepts both max_tokens and the newer max_completion_tokens", () => {
    expect(ChatCompletionRequest.parse({ ...MINIMAL, max_tokens: 100 }).max_tokens).toBe(100);
    expect(
      ChatCompletionRequest.parse({ ...MINIMAL, max_completion_tokens: 100 }).max_completion_tokens,
    ).toBe(100);
  });

  it("rejects a non-positive max_tokens", () => {
    expect(ChatCompletionRequest.safeParse({ ...MINIMAL, max_tokens: 0 }).success).toBe(false);
  });

  it("accepts stream_options.include_usage", () => {
    const parsed = ChatCompletionRequest.parse({
      ...MINIMAL,
      stream: true,
      stream_options: { include_usage: true },
    });
    expect(parsed.stream_options?.include_usage).toBe(true);
  });

  it("rejects more than four stop sequences, matching the OpenAI limit", () => {
    expect(
      ChatCompletionRequest.safeParse({ ...MINIMAL, stop: ["a", "b", "c", "d"] }).success,
    ).toBe(true);
    expect(
      ChatCompletionRequest.safeParse({ ...MINIMAL, stop: ["a", "b", "c", "d", "e"] }).success,
    ).toBe(false);
  });
});

describe("sseFrame", () => {
  it("emits a single frame terminated by a blank line", () => {
    const chunk: ChatCompletionChunk = {
      id: "chatcmpl-1",
      object: "chat.completion.chunk",
      created: 1,
      model: "m",
      choices: [{ index: 0, delta: { content: "hi" }, finish_reason: null }],
    };
    const frame = sseFrame(chunk);
    expect(frame.startsWith("data: ")).toBe(true);
    expect(frame.endsWith("\n\n")).toBe(true);
    expect(JSON.parse(frame.slice(6, -2))).toEqual(chunk);
  });

  it("escapes newlines in content so one delta stays one frame", () => {
    // A raw newline inside the payload would split the SSE frame and corrupt
    // the stream for every client. JSON.stringify is what prevents that.
    const frame = sseFrame({ text: "line1\nline2\n\nline3" });
    expect(frame.split("\n\n")).toHaveLength(2);
  });

  it("terminates streams with the literal [DONE] sentinel", () => {
    expect(SSE_DONE).toBe("data: [DONE]\n\n");
  });
});

describe("errorBody", () => {
  it("produces the OpenAI error envelope", () => {
    expect(errorBody("nope", "invalid_request_error", "bad_model", "model")).toEqual({
      error: { message: "nope", type: "invalid_request_error", param: "model", code: "bad_model" },
    });
  });
});
