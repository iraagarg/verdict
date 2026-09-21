/**
 * The GitHub App webhook receiver.
 *
 * This is the App half of D-054: the Action is what actually runs on PRs, and
 * this exists so the webhook security work is real and reviewable rather than
 * described. It needs a public HTTPS endpoint to receive anything, which is
 * why D-005 preferred the Action for demonstrating the feature.
 *
 * The ordering in `POST /webhook` is the security-relevant part and is
 * deliberate:
 *   1. read the RAW body (never the parsed object)
 *   2. verify the signature
 *   3. only then parse
 *   4. reject replays
 *   5. only then do any work
 *
 * Parsing before verifying would run untrusted input through a parser on an
 * endpoint anyone can POST to.
 */
import Fastify, { type FastifyInstance } from "fastify";
import { z } from "zod";
import { DeliveryLog, touchesPrompts, verifySignature } from "./signature.js";

export const WebhookEnv = z
  .object({
    GITHUB_WEBHOOK_SECRET: z
      .string()
      .min(16, "a webhook secret shorter than 16 chars is not a secret"),
    GHAPP_PORT: z.coerce.number().int().min(1).max(65535).default(8090),
    PROMPTS_PREFIX: z.string().default("prompts/"),
    EVAL_CAP_USD: z.coerce.number().positive().default(1.0),
  })
  .strict();

export type WebhookEnv = z.infer<typeof WebhookEnv>;

/** Only the fields we act on. GitHub sends a great deal more. */
const PullRequestEvent = z
  .object({
    action: z.string(),
    number: z.number().int().positive(),
    repository: z.object({ full_name: z.string() }).passthrough(),
    pull_request: z.object({ head: z.object({ sha: z.string() }).passthrough() }).passthrough(),
  })
  .passthrough();

export interface Deps {
  env: WebhookEnv;
  /** Files changed in the PR. Injected so the handler is testable without GitHub. */
  changedFiles: (repo: string, prNumber: number) => Promise<string[]>;
  /** Run the eval and post the comment. Injected for the same reason. */
  runEval: (input: { repo: string; prNumber: number; sha: string }) => Promise<void>;
  deliveries?: DeliveryLog;
}

export function buildWebhookApp(deps: Deps): FastifyInstance {
  const app = Fastify({ logger: { level: "info" } });
  const deliveries = deps.deliveries ?? new DeliveryLog();

  // Keep the raw bytes. Verifying a re-serialised object is the classic
  // mistake: JSON round-tripping can change the bytes and therefore the HMAC.
  app.addContentTypeParser("application/json", { parseAs: "buffer" }, (_req, body, done) =>
    done(null, body),
  );

  app.get("/health", async () => ({ status: "ok", service: "ghapp" }));

  app.post("/webhook", async (req, reply) => {
    const raw = req.body as Buffer;

    const verified = verifySignature(
      raw,
      req.headers["x-hub-signature-256"] as string | undefined,
      deps.env.GITHUB_WEBHOOK_SECRET,
    );
    if (!verified.ok) {
      // 401 and a reason code, never an explanation of what differed.
      req.log.warn({ reason: verified.reason }, "rejected webhook");
      return reply.code(401).send({ error: "invalid signature", reason: verified.reason });
    }

    const deliveryId = req.headers["x-github-delivery"];
    if (typeof deliveryId !== "string" || deliveryId === "") {
      return reply.code(400).send({ error: "missing X-GitHub-Delivery" });
    }
    if (!deliveries.accept(deliveryId)) {
      // A retry or a replay. Both must be no-ops, and 200 stops GitHub retrying.
      req.log.info({ deliveryId }, "duplicate delivery ignored");
      return reply.code(200).send({ status: "duplicate", deliveryId });
    }

    const event = req.headers["x-github-event"];
    if (event !== "pull_request") {
      return reply.code(200).send({ status: "ignored", event });
    }

    const parsed = PullRequestEvent.safeParse(JSON.parse(raw.toString("utf8")));
    if (!parsed.success) {
      return reply.code(400).send({ error: "unexpected pull_request payload" });
    }
    const pr = parsed.data;

    if (!["opened", "synchronize", "reopened"].includes(pr.action)) {
      return reply.code(200).send({ status: "ignored", action: pr.action });
    }

    const files = await deps.changedFiles(pr.repository.full_name, pr.number);
    if (!touchesPrompts(files, deps.env.PROMPTS_PREFIX)) {
      // Evals cost money. A PR that changes no prompts gets none.
      return reply.code(200).send({ status: "no_prompt_changes", files: files.length });
    }

    await deps.runEval({
      repo: pr.repository.full_name,
      prNumber: pr.number,
      sha: pr.pull_request.head.sha,
    });
    return reply.code(202).send({ status: "eval_started", pr: pr.number });
  });

  return app;
}
