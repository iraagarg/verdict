/**
 * POST /v1/chat/completions — the OpenAI-compatible surface.
 *
 * Three things here are load-bearing and worth reading carefully:
 *
 * 1. **Retry happens only before the first byte reaches the client.** Once a
 *    single delta has been written we are committed: there is no way to "un-say"
 *    text a client has already rendered. So the retry wraps *opening the stream
 *    and pulling the first event*, not the whole stream. After that, a failure
 *    is a truncated stream, not a retry.
 *
 * 2. **Cost is metered during the stream, never after.** The meter is updated on
 *    every usage-bearing event as it arrives; when the stream ends the number is
 *    already final. There is no second pass and no re-parsing of the response.
 *
 * 3. **A client disconnect aborts the upstream call.** Otherwise we keep paying
 *    for tokens nobody will ever read. DESIGN.md failure mode #4.
 */
import { createHash, randomUUID } from "node:crypto";
import type { FastifyReply, FastifyRequest } from "fastify";
import {
  ChatCompletionRequest,
  SSE_DONE,
  errorBody,
  getModel,
  sseFrame,
  type ChatCompletion,
  type ChatCompletionChunk,
  type FinishReason,
  type OpenAIErrorBody,
} from "@verdict/shared";
import { CostMeter } from "../cost/meter.js";
import { withRetry } from "../resilience/retry.js";
import { CircuitOpenError } from "../resilience/breaker.js";
import { IncompatibleParameterError, normalizeRequest } from "../providers/normalize.js";
import { ProviderError, type ProviderEvent } from "../providers/types.js";
import type { GatewayApp } from "../http.js";
import type { GatewayServices } from "../services.js";
import type { TraceRow } from "../trace/repository.js";

/** Delegates model choice to the router. P1 resolves it to safe_default; P5 fits a policy. */
const AUTO_MODEL = "verdict-auto";

function canonicalRequestHash(body: unknown): Buffer {
  return createHash("sha256").update(JSON.stringify(body)).digest();
}

function chunk(
  id: string,
  model: string,
  created: number,
  delta: ChatCompletionChunk["choices"][0]["delta"],
  finishReason: FinishReason | null,
): ChatCompletionChunk {
  return {
    id,
    object: "chat.completion.chunk",
    created,
    model,
    choices: [{ index: 0, delta, finish_reason: finishReason }],
  };
}

/** Map a provider failure to the HTTP status an OpenAI client expects. */
function statusFor(err: unknown): { status: number; body: OpenAIErrorBody; errorKind: string } {
  if (err instanceof CircuitOpenError) {
    return {
      status: 503,
      body: errorBody(err.message, "server_error", "circuit_open"),
      errorKind: "breaker_open",
    };
  }
  if (err instanceof ProviderError) {
    const f = err.failure;
    switch (f.kind) {
      case "rate_limited":
        return {
          status: 429,
          body: errorBody(f.message, "rate_limit_error", "rate_limited"),
          errorKind: "rate_limited",
        };
      case "timeout":
        return {
          status: 504,
          body: errorBody(f.message, "server_error", "timeout"),
          errorKind: "timeout",
        };
      case "auth":
        return {
          status: 502,
          body: errorBody("upstream authentication failed", "server_error", "upstream_auth"),
          errorKind: "provider_auth",
        };
      case "bad_request":
        return {
          status: 400,
          body: errorBody(f.message, "invalid_request_error", "upstream_bad_request"),
          errorKind: "upstream_bad_request",
        };
      case "provider_5xx":
      case "connection":
      case "unknown":
      default:
        return {
          status: 502,
          body: errorBody(f.message, "server_error", "upstream_error"),
          errorKind: f.kind,
        };
    }
  }
  if (err instanceof IncompatibleParameterError) {
    return {
      status: 400,
      body: errorBody(err.message, "invalid_request_error", "unsupported_parameter", err.param),
      errorKind: "unsupported_parameter",
    };
  }
  return {
    status: 500,
    body: errorBody("internal error", "server_error", "internal_error"),
    errorKind: "internal",
  };
}

export function registerChatRoutes(app: GatewayApp, services: GatewayServices): void {
  const { env, registry, traces } = services;

  app.post("/v1/chat/completions", async (req: FastifyRequest, reply: FastifyReply) => {
    const startedAt = Date.now();
    const requestId = String(req.id);

    // ── validate ────────────────────────────────────────────────────────────
    const parsed = ChatCompletionRequest.safeParse(req.body);
    if (!parsed.success) {
      const issue = parsed.error.issues[0];
      return reply
        .code(400)
        .send(
          errorBody(
            issue ? `${issue.path.join(".") || "body"}: ${issue.message}` : "invalid request",
            "invalid_request_error",
            "invalid_request",
            issue?.path[0] === undefined ? null : String(issue.path[0]),
          ),
        );
    }
    const body = parsed.data;

    // ── resolve model ───────────────────────────────────────────────────────
    // P1 has no fitted policy, so `verdict-auto` resolves to safe_default.
    // P5 replaces this with the nearest-route lookup. The fallback direction is
    // the point: ambiguity always resolves toward quality (DESIGN.md §8.2).
    const modelWasPinned = body.model !== AUTO_MODEL;
    const modelId = modelWasPinned ? body.model : registry.config.safe_default;

    if (registry.config.models[modelId] === undefined) {
      return reply
        .code(404)
        .send(
          errorBody(
            `The model \`${modelId}\` does not exist or is not configured in this gateway.`,
            "invalid_request_error",
            "model_not_found",
            "model",
          ),
        );
    }
    if (!registry.isServable(modelId)) {
      return reply
        .code(503)
        .send(
          errorBody(
            `No API key is configured for the provider that serves \`${modelId}\`.`,
            "server_error",
            "provider_unconfigured",
            "model",
          ),
        );
    }

    const resolved = registry.resolve(modelId);
    const entry = getModel(registry.config, modelId);

    // ── normalise (drops params the target model rejects — D-010) ───────────
    let normalized;
    try {
      normalized = normalizeRequest(body, modelId, entry, {
        modelWasPinned,
        defaultMaxOutputTokens: env.DEFAULT_MAX_OUTPUT_TOKENS,
      });
    } catch (err) {
      const mapped = statusFor(err);
      return reply.code(mapped.status).send(mapped.body);
    }

    const meter = new CostMeter(modelId, entry);
    const created = Math.floor(startedAt / 1000);
    const completionId = `chatcmpl-${randomUUID()}`;

    // ── abort wiring: client hangs up → upstream call is cancelled ──────────
    //
    // The listener goes on the RESPONSE, not the request. `req.raw` is an
    // IncomingMessage, and its "close" event fires as soon as the request body
    // has been fully read — which for a normal POST is immediately, long before
    // the client has gone anywhere. Listening there makes every streaming
    // request look like an instant disconnect: it aborts its own upstream call
    // and returns an empty 200.
    //
    // `reply.raw` closing while `writableEnded` is still false is the real
    // signal that the peer went away mid-response.
    const ac = new AbortController();
    let clientGone = false;
    const onClose = (): void => {
      if (reply.raw.writableEnded) return; // normal completion, not a disconnect
      clientGone = true;
      ac.abort();
    };
    reply.raw.on("close", onClose);

    const wholeRequestTimer = setTimeout(() => ac.abort(), env.REQUEST_TIMEOUT_MS);

    let ttftMs: number | null = null;
    let finishReason: FinishReason | null = null;
    let errorKind: string | null = null;
    let status = 200;
    let text = "";
    let thinking = "";

    const recordTrace = (responseBody: unknown): void => {
      const snapshot = meter.snapshot();
      const row: TraceRow = {
        id: randomUUID(),
        requestId,
        createdAt: new Date(startedAt),
        routeKey: null, // P5 fills this in
        policyVersion: null,
        modelRequested: body.model,
        modelServed: modelId,
        provider: resolved.adapter.name,
        streamed: body.stream,
        requestHash: canonicalRequestHash(req.body),
        requestBody: req.body,
        responseBody,
        status,
        errorKind,
        finishReason,
        cacheHit: "none", // P6
        inputTokens: snapshot.inputTokens,
        outputTokens: snapshot.outputTokens,
        cacheReadTokens: snapshot.cacheReadTokens,
        cacheWriteTokens: snapshot.cacheWriteTokens,
        costUsd: snapshot.costUsd,
        usageIsFinal: snapshot.isFinal,
        latencyMs: Date.now() - startedAt,
        ttftMs,
      };
      traces.enqueue(row); // never blocks, never throws
    };

    /**
     * Open the upstream stream and pull the FIRST event. Everything inside this
     * function is retryable because nothing has reached the client yet.
     */
    const openStream = async (): Promise<{
      iterator: AsyncIterator<ProviderEvent>;
      first: IteratorResult<ProviderEvent>;
    }> => {
      resolved.breaker.assertCanAttempt();
      try {
        const iterable = resolved.adapter.streamChat(normalized.request, entry, {
          signal: ac.signal,
          timeoutMs: env.REQUEST_TIMEOUT_MS,
          requestId,
        });
        const iterator = iterable[Symbol.asyncIterator]();

        // First-token deadline, distinct from the whole-request deadline.
        const ttftTimer = setTimeout(() => ac.abort(), env.TTFT_TIMEOUT_MS);
        try {
          const first = await iterator.next();
          resolved.breaker.onSuccess();
          return { iterator, first };
        } finally {
          clearTimeout(ttftTimer);
        }
      } catch (err) {
        resolved.breaker.onFailure();
        throw err;
      }
    };

    let opened: Awaited<ReturnType<typeof openStream>>;
    try {
      opened = await withRetry(openStream, {
        retries: env.MAX_RETRIES,
        baseDelayMs: env.RETRY_BASE_DELAY_MS,
        maxDelayMs: env.RETRY_MAX_DELAY_MS,
        signal: ac.signal,
        isRetryable: (err) => err instanceof ProviderError && err.failure.retryable,
        retryAfterMs: (err) =>
          err instanceof ProviderError ? err.failure.retryAfterMs : undefined,
        onRetry: ({ attempt, delayMs, err }) =>
          req.log.warn(
            { attempt, delay_ms: delayMs, err: String(err), model: modelId },
            "retrying upstream",
          ),
      });
    } catch (err) {
      clearTimeout(wholeRequestTimer);
      reply.raw.off("close", onClose);

      if (clientGone) {
        // The client is gone, so there is nobody to send an error to. Take the
        // response over and close it rather than asking Fastify to write to a
        // dead socket.
        errorKind = "client_abort";
        status = 499;
        recordTrace(null);
        reply.hijack();
        if (!reply.raw.writableEnded) reply.raw.end();
        return;
      }
      const mapped = statusFor(err);
      status = mapped.status;
      errorKind = mapped.errorKind;
      recordTrace(null);
      req.log.error({ err: String(err), model: modelId, error_kind: errorKind }, "upstream failed");
      return reply.code(mapped.status).send(mapped.body);
    }

    // ── committed: from here a failure is a truncated stream, not a retry ────
    const consume = async function* (): AsyncGenerator<ProviderEvent> {
      if (!opened.first.done) yield opened.first.value;
      while (true) {
        const next = await opened.iterator.next();
        if (next.done) break;
        yield next.value;
      }
    };

    /** Non-streaming responses only; the streaming path sets raw headers itself. */
    const commonHeaders = (): void => {
      void reply.header("x-verdict-model-served", modelId);
      void reply.header("x-verdict-provider", resolved.adapter.name);
      void reply.header("x-verdict-route", "unrouted");
      if (normalized.droppedParams.length > 0) {
        void reply.header("x-verdict-dropped-params", normalized.droppedParams.join(","));
      }
    };

    // ── streaming path ──────────────────────────────────────────────────────
    if (body.stream) {
      // hijack() must happen BEFORE the first write to reply.raw. It tells
      // Fastify to stop managing this response; without it Fastify still tries
      // to serialise and send a reply for a socket we are already writing to,
      // and the response never terminates.
      reply.hijack();

      reply.raw.setHeader("content-type", "text/event-stream; charset=utf-8");
      reply.raw.setHeader("cache-control", "no-cache, no-transform");
      reply.raw.setHeader("connection", "keep-alive");
      reply.raw.setHeader("x-verdict-request-id", requestId);
      reply.raw.setHeader("x-verdict-model-served", modelId);
      reply.raw.setHeader("x-verdict-provider", resolved.adapter.name);
      if (normalized.droppedParams.length > 0) {
        reply.raw.setHeader("x-verdict-dropped-params", normalized.droppedParams.join(","));
      }
      reply.raw.flushHeaders();

      const write = (payload: string): void => {
        if (!reply.raw.writableEnded) reply.raw.write(payload);
      };

      let roleSent = false;
      try {
        for await (const event of consume()) {
          if (clientGone) break;

          switch (event.type) {
            case "start":
              if (event.usage) meter.observe(event.usage);
              break;

            case "text": {
              if (ttftMs === null) ttftMs = Date.now() - startedAt;
              if (!roleSent) {
                roleSent = true;
                write(
                  sseFrame(
                    chunk(completionId, modelId, created, { role: "assistant", content: "" }, null),
                  ),
                );
              }
              text += event.delta;
              write(
                sseFrame(chunk(completionId, modelId, created, { content: event.delta }, null)),
              );
              break;
            }

            case "thinking":
              // Recorded, never forwarded: no OpenAI client expects a thinking
              // block. Its tokens are still billed via output_tokens.
              thinking += event.delta;
              break;

            case "usage":
              meter.observe(event.usage);
              break;

            case "finish": {
              if (event.usage) meter.observe(event.usage);
              meter.markFinal();
              finishReason = event.finishReason;
              if (!roleSent) {
                write(
                  sseFrame(
                    chunk(completionId, modelId, created, { role: "assistant", content: "" }, null),
                  ),
                );
              }
              write(sseFrame(chunk(completionId, modelId, created, {}, event.finishReason)));
              if (body.stream_options?.include_usage === true) {
                const s = meter.snapshot();
                write(
                  sseFrame({
                    ...chunk(completionId, modelId, created, {}, null),
                    choices: [],
                    usage: {
                      prompt_tokens: s.inputTokens,
                      completion_tokens: s.outputTokens,
                      total_tokens: s.inputTokens + s.outputTokens,
                    },
                  }),
                );
              }
              break;
            }
          }
        }

        if (clientGone) {
          errorKind = "client_abort";
          status = 499;
        } else if (finishReason === null) {
          // Upstream closed before its terminal event. Emit a well-formed end
          // so the client's parser terminates instead of hanging forever.
          // DESIGN.md failure mode #3.
          errorKind = "stream_abort";
          finishReason = "length";
          write(sseFrame(chunk(completionId, modelId, created, {}, "length")));
        }
      } catch (err) {
        if (clientGone || ac.signal.aborted) {
          errorKind = clientGone ? "client_abort" : "timeout";
          status = clientGone ? 499 : 504;
        } else {
          // Past the commit point, every upstream failure is the same thing to
          // the client: a truncated stream. The provider-specific cause is
          // useful for debugging, so it goes in the log, but the trace records
          // what actually happened to the response. DESIGN.md failure mode #3.
          errorKind = "stream_abort";
          finishReason = "length";
          write(sseFrame(chunk(completionId, modelId, created, {}, "length")));
          req.log.error(
            { err: String(err), cause_kind: statusFor(err).errorKind, error_kind: errorKind },
            "stream failed after first byte",
          );
        }
      } finally {
        clearTimeout(wholeRequestTimer);
        reply.raw.off("close", onClose);
        // Abort upstream unconditionally: if we stopped consuming for any
        // reason, we must stop paying for tokens too.
        ac.abort();

        if (!reply.raw.writableEnded) {
          if (!clientGone) write(SSE_DONE);
          reply.raw.end();
        }
        recordTrace({ text, thinking_chars: thinking.length });
      }

      return;
    }

    // ── non-streaming path ──────────────────────────────────────────────────
    // Still consumes the upstream stream event by event so the meter stays
    // incremental; only the client-facing response is aggregated.
    try {
      for await (const event of consume()) {
        switch (event.type) {
          case "start":
            if (event.usage) meter.observe(event.usage);
            break;
          case "text":
            if (ttftMs === null) ttftMs = Date.now() - startedAt;
            text += event.delta;
            break;
          case "thinking":
            thinking += event.delta;
            break;
          case "usage":
            meter.observe(event.usage);
            break;
          case "finish":
            if (event.usage) meter.observe(event.usage);
            meter.markFinal();
            finishReason = event.finishReason;
            break;
        }
      }
    } catch (err) {
      clearTimeout(wholeRequestTimer);
      reply.raw.off("close", onClose);
      ac.abort();
      const mapped = statusFor(err);
      status = mapped.status;
      errorKind = mapped.errorKind;
      recordTrace(null);
      return reply.code(mapped.status).send(mapped.body);
    }

    clearTimeout(wholeRequestTimer);
    reply.raw.off("close", onClose);
    ac.abort();

    if (finishReason === null) {
      errorKind = "stream_abort";
      finishReason = "length";
    }

    const snapshot = meter.snapshot();
    const completion: ChatCompletion = {
      id: completionId,
      object: "chat.completion",
      created,
      model: modelId,
      choices: [
        { index: 0, message: { role: "assistant", content: text }, finish_reason: finishReason },
      ],
      usage: {
        prompt_tokens: snapshot.inputTokens,
        completion_tokens: snapshot.outputTokens,
        total_tokens: snapshot.inputTokens + snapshot.outputTokens,
      },
    };

    commonHeaders();
    void reply.header("x-verdict-cost-usd", snapshot.costUsd.toFixed(8));
    void reply.header("x-verdict-usage-final", String(snapshot.isFinal));
    recordTrace(completion);
    return reply.code(200).send(completion);
  });
}
