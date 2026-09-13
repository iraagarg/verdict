/**
 * Structured JSON logging.
 *
 * Non-negotiable #7: structured JSON logs with a request id propagated end to
 * end. Every log line carries `request_id`, and the same id goes back to the
 * client in `x-verdict-request-id` so a user-reported problem can be traced
 * without asking them for a timestamp.
 */
import pino from "pino";
import type { Env } from "./env.js";

export function createLogger(env: Env): pino.Logger {
  return pino({
    level: env.LOG_LEVEL,
    // Fastify's default key is `reqId`; `request_id` matches the trace column
    // and the response header, so one grep finds all three.
    messageKey: "message",
    formatters: {
      level: (label) => ({ level: label }),
    },
    redact: {
      paths: [
        "req.headers.authorization",
        "req.headers['x-api-key']",
        "req.headers.cookie",
        "*.api_key",
        "*.apiKey",
      ],
      censor: "[redacted]",
    },
    base: { service: "gateway" },
  });
}
