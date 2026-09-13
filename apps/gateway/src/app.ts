/**
 * Fastify application factory.
 *
 * P0 scaffold: health and readiness only. The OpenAI-compatible surface
 * (POST /v1/chat/completions) lands in P1 — see DESIGN.md §6.
 */
import Fastify, { LogController } from "fastify";
import { randomUUID } from "node:crypto";
import { createLogger } from "./logger.js";
import type { Env } from "./env.js";

export interface BuildOptions {
  env: Env;
}

/** Reported by /health so a running container can be tied back to a commit. */
const GIT_SHA = process.env["GIT_SHA"] ?? "unknown";

/**
 * The concrete instance type. Annotating buildApp with the bare
 * `FastifyInstance` default loses the pino logger type and fails under
 * `exactOptionalPropertyTypes`, so the type is derived from the factory rather
 * than asserted onto it.
 */
export type GatewayApp = ReturnType<typeof buildApp>;

export function buildApp({ env }: BuildOptions) {
  const app = Fastify({
    loggerInstance: createLogger(env),
    // Trust an inbound request id when a caller supplies one, so a trace can
    // span the dashboard, the gateway and evald. Mint one otherwise.
    genReqId: (req) => {
      const header = req.headers["x-request-id"];
      if (typeof header === "string" && header.length > 0 && header.length <= 200) return header;
      return randomUUID();
    },
    requestIdHeader: false,
    // Fastify 5 moved these off the top level; the top-level options are
    // deprecated and go away in Fastify 6.
    logController: new LogController({
      requestIdLogLabel: "request_id",
      disableRequestLogging: false,
    }),
    bodyLimit: 4 * 1024 * 1024,
  });

  // The request id is echoed on every response, success or failure.
  app.addHook("onRequest", async (req, reply) => {
    void reply.header("x-verdict-request-id", req.id);
  });

  /**
   * Liveness. Answers "is this process running?" and must not touch a
   * datastore — a Postgres outage does not mean the gateway should be killed
   * and restarted. DESIGN.md failure mode #7.
   */
  app.get("/health", async () => ({
    status: "ok",
    service: "gateway",
    git_sha: GIT_SHA,
    uptime_s: Math.floor(process.uptime()),
  }));

  /**
   * Readiness. Answers "should traffic be sent here?".
   *
   * P0 returns the dependency checks as an empty set. When P1 adds Postgres and
   * Redis, degraded dependencies belong here — but per DESIGN.md failure modes
   * #7 and #8 a degraded datastore must NOT make the gateway unready, because
   * Verdict serves traffic without its analytics store. Only a condition that
   * genuinely prevents serving may return 503.
   */
  app.get("/ready", async () => ({
    status: "ready",
    dependencies: {} as Record<string, "ok" | "degraded">,
  }));

  app.setNotFoundHandler(async (req, reply) => {
    // OpenAI-shaped error body, so a client's error parser keeps working.
    return reply.code(404).send({
      error: {
        message: `Unknown route: ${req.method} ${req.url}`,
        type: "invalid_request_error",
        param: null,
        code: "not_found",
      },
    });
  });

  return app;
}
