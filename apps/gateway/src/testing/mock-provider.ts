/**
 * A real HTTP server that speaks Anthropic's streaming wire protocol.
 *
 * Tests point the Anthropic SDK's `baseURL` at this, so the path under test is
 * the genuine one: real SDK, real SSE parsing, real socket. A hand-stubbed
 * adapter would test our mock instead of our code — in particular it could not
 * reproduce a mid-stream socket destruction, which is the case that matters
 * most here.
 */
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

export interface MockScript {
  /** Emitted in order as SSE frames. */
  events?: unknown[];
  /** Destroy the socket after this many events, simulating a mid-stream abort. */
  destroyAfter?: number;
  /** Milliseconds between frames. */
  delayMs?: number;
  /** Respond with this status and body instead of streaming. */
  failWith?: { status: number; body: unknown; headers?: Record<string, string> };
  /** Hold the connection open without sending anything, to exercise timeouts. */
  hang?: boolean;
}

export interface MockProvider {
  url: string;
  /** Requests received, in order. Used to assert retry behaviour. Empty when `record` is false. */
  requests: Array<{ body: unknown; headers: Record<string, string | string[] | undefined> }>;
  /** Requests received. Accurate even when bodies are not retained. */
  attemptCount: () => number;
  /** Resolves when the server observes the client disconnecting mid-stream. */
  disconnected: Promise<void>;
  /** How many frames were written before the connection ended. */
  framesWritten: () => number;
  setScript: (script: MockScript | ((attempt: number) => MockScript)) => void;
  close: () => Promise<void>;
}

/** Anthropic-shaped events for a simple two-token completion. */
export function anthropicScript(
  texts: string[],
  usage: { input: number; output: number; cacheRead?: number; cacheWrite?: number } = {
    input: 10,
    output: 5,
  },
): unknown[] {
  return [
    {
      type: "message_start",
      message: {
        id: "msg_mock_1",
        type: "message",
        role: "assistant",
        model: "claude-haiku-4-5",
        content: [],
        stop_reason: null,
        stop_sequence: null,
        usage: {
          input_tokens: usage.input,
          output_tokens: 1,
          cache_read_input_tokens: usage.cacheRead ?? 0,
          cache_creation_input_tokens: usage.cacheWrite ?? 0,
        },
      },
    },
    { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } },
    ...texts.map((t) => ({
      type: "content_block_delta",
      index: 0,
      delta: { type: "text_delta", text: t },
    })),
    { type: "content_block_stop", index: 0 },
    {
      type: "message_delta",
      delta: { stop_reason: "end_turn", stop_sequence: null },
      usage: { output_tokens: usage.output },
    },
    { type: "message_stop" },
  ];
}

export interface MockOptions {
  /**
   * Retain every request in `requests` for assertions. Default true, which is
   * what tests need.
   *
   * The load test drives hundreds of thousands of requests through this same
   * server, and retaining each parsed body would grow the heap until the
   * upstream — not the gateway — became the bottleneck. That would silently
   * corrupt the very measurement the load test exists to take, so it passes
   * false. `attemptCount` stays accurate either way, because the retry tests
   * index scripts by attempt number.
   */
  record?: boolean;
  /** Bind to a fixed port instead of an ephemeral one. Used by the load test. */
  port?: number;
}

export async function startMockProvider(
  initial: MockScript = {},
  options: MockOptions = {},
): Promise<MockProvider> {
  const record = options.record ?? true;
  let script: MockScript | ((attempt: number) => MockScript) = initial;
  const requests: MockProvider["requests"] = [];
  let attempts = 0;
  let frames = 0;
  let resolveDisconnect: () => void;
  const disconnected = new Promise<void>((r) => {
    resolveDisconnect = r;
  });

  const server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      const attempt = attempts++;
      if (record) {
        requests.push({
          body: raw.length > 0 ? (JSON.parse(raw) as unknown) : null,
          headers: req.headers,
        });
      }

      const s = typeof script === "function" ? script(attempt) : script;

      if (s.failWith) {
        res.writeHead(s.failWith.status, {
          "content-type": "application/json",
          ...(s.failWith.headers ?? {}),
        });
        res.end(JSON.stringify(s.failWith.body));
        return;
      }

      res.writeHead(200, {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        connection: "keep-alive",
      });

      if (s.hang) return; // never write, never end

      const events = s.events ?? [];
      let i = 0;
      let closed = false;

      // The client going away mid-stream is the case this mock exists for.
      res.on("close", () => {
        if (!closed && i < events.length) {
          closed = true;
          resolveDisconnect();
        }
      });

      const tick = (): void => {
        if (closed || res.writableEnded) return;
        if (s.destroyAfter !== undefined && i >= s.destroyAfter) {
          closed = true;
          res.socket?.destroy(); // abrupt: no terminal event, no clean close
          return;
        }
        if (i >= events.length) {
          res.end();
          return;
        }
        const event = events[i++] as { type?: string };
        res.write(`event: ${String(event.type)}\ndata: ${JSON.stringify(event)}\n\n`);
        frames++;
        setTimeout(tick, s.delayMs ?? 0);
      };
      tick();
    });
  });

  await new Promise<void>((resolve) => server.listen(options.port ?? 0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;

  return {
    url: `http://127.0.0.1:${port}`,
    requests,
    attemptCount: () => attempts,
    disconnected,
    framesWritten: () => frames,
    setScript: (s) => {
      script = s;
    },
    close: () =>
      new Promise<void>((resolve) => {
        (server as Server).closeAllConnections?.();
        server.close(() => resolve());
      }),
  };
}
