/**
 * Environment validation.
 *
 * Non-negotiable #6: the process must refuse to start with a missing or
 * malformed key, and must never fail later at request time. `loadEnv` is pure
 * and testable; `index.ts` is the only place that calls `process.exit`.
 *
 * DESIGN.md failure mode #15.
 */
import { z } from "zod";

/**
 * A provider key.
 *
 * Blank means UNSET, not invalid. `docker compose` and most CI systems pass an
 * undeclared variable through as an empty string (`FOO: ${FOO:-}`), so treating
 * "" as a validation failure would make the stack refuse to start whenever the
 * operator simply has no Groq account. The meaningful guard is the
 * at-least-one-provider check below, which still fires when every key is blank.
 *
 * A key that is present but obviously a placeholder IS an error — it passes a
 * naive presence check and then fails at request time, which is exactly what
 * non-negotiable #6 exists to prevent.
 *
 * apps/evald/src/evald/settings.py implements the same two rules; the services
 * must agree, or `docker compose up` half-works.
 */
const apiKey = z.preprocess(
  (v) => (typeof v === "string" && v.trim() === "" ? undefined : v),
  z
    .string()
    .trim()
    .min(1)
    .refine((v) => !/^(your|changeme|placeholder|xxx|todo|<.*>)/i.test(v), {
      message: "looks like a placeholder rather than a real key",
    })
    .optional(),
);

const port = z.coerce.number().int().min(1).max(65535);

/** A path that may be absent, where an empty string also means absent. */
const optionalPath = z.preprocess(
  (v) => (typeof v === "string" && v.trim() === "" ? undefined : v),
  z.string().min(1).optional(),
);

/** A URL that may be absent, where an empty string also means absent. */
const optionalUrl = z.preprocess(
  (v) => (typeof v === "string" && v.trim() === "" ? undefined : v),
  z.string().url().optional(),
);

export const EnvSchema = z
  .object({
    NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
    LOG_LEVEL: z.enum(["fatal", "error", "warn", "info", "debug", "trace"]).default("info"),

    GATEWAY_PORT: port.default(8080),
    GATEWAY_HOST: z.string().min(1).default("0.0.0.0"),

    DATABASE_URL: z
      .string()
      .url()
      .refine((v) => v.startsWith("postgres://") || v.startsWith("postgresql://"), {
        message: "must be a postgres:// or postgresql:// URL",
      }),
    /**
     * OPTIONAL, deliberately.
     *
     * Redis was to be the hot path of the semantic cache. P6 measured that cache
     * against 200 paraphrases and 600 hard negatives and found no threshold that
     * was both safe and useful (D-046, D-047), so it is not deployed and no
     * Redis client is installed. Requiring a connection string the process will
     * never open would mean every deployment provisions a service to satisfy a
     * schema — the dependency outliving the feature that justified it.
     *
     * The validation stays because the shape is still worth checking the moment
     * a value IS supplied: a wrong URL should fail at boot, not on the first
     * cache read. Made optional in P8 (D-059).
     */
    REDIS_URL: z.preprocess(
      // Blank means absent, matching every other optional here: compose passes
      // `${VAR:-}` as "" and that must not read as a malformed URL (D-013).
      (v) => (typeof v === "string" && v.trim() === "" ? undefined : v),
      z
        .string()
        .url()
        .refine((v) => v.startsWith("redis://") || v.startsWith("rediss://"), {
          message: "must be a redis:// or rediss:// URL",
        })
        .optional(),
    ),

    ANTHROPIC_API_KEY: apiKey,
    OPENAI_API_KEY: apiKey,
    GROQ_API_KEY: apiKey,

    COST_CAP_USD_PER_DAY: z.coerce.number().positive(),

    // --- provider base URLs (overridden in tests to point at a mock server) ---
    // Blank means unset, for the same reason provider keys do: compose passes
    // every undeclared variable through as "".
    ANTHROPIC_BASE_URL: optionalUrl,
    OPENAI_BASE_URL: optionalUrl,
    GROQ_BASE_URL: optionalUrl,

    // --- timeouts (milliseconds) ---
    /** Whole upstream call, including streaming. */
    REQUEST_TIMEOUT_MS: z.coerce.number().int().positive().default(120_000),
    /** How long we wait for the provider's first event before giving up. */
    TTFT_TIMEOUT_MS: z.coerce.number().int().positive().default(30_000),

    // --- retry ---
    /** RETRIES after the initial attempt. The gateway owns this, not the SDKs (D-013). */
    MAX_RETRIES: z.coerce.number().int().min(0).max(5).default(2),
    RETRY_BASE_DELAY_MS: z.coerce.number().int().positive().default(250),
    RETRY_MAX_DELAY_MS: z.coerce.number().int().positive().default(8_000),

    // --- circuit breaker ---
    BREAKER_FAILURE_THRESHOLD: z.coerce.number().int().min(1).default(5),
    BREAKER_COOLDOWN_MS: z.coerce.number().int().min(0).default(30_000),

    // --- trace write-behind queue (D-007) ---
    TRACE_QUEUE_CAPACITY: z.coerce.number().int().min(1).default(10_000),
    TRACE_BATCH_SIZE: z.coerce.number().int().min(1).default(25),
    TRACE_FLUSH_INTERVAL_MS: z.coerce.number().int().positive().default(1_000),

    PG_POOL_MAX: z.coerce.number().int().min(1).default(10),

    /** Applied when the client sends no max_tokens; capped by the model's own limit. */
    DEFAULT_MAX_OUTPUT_TOKENS: z.coerce.number().int().positive().default(16_384),

    /** Path to the model ladder. Absolute in Docker, relative in dev. */
    MODELS_CONFIG_PATH: z.string().min(1).default("config/models.yaml"),

    // --- router (P5) ---
    /**
     * Feature flag. "off" serves safe_default for every auto-routed request,
     * which is the behaviour before a policy exists. Default off: a fitted
     * policy must be switched on deliberately, never inherited by accident.
     */
    ROUTER_MODE: z.enum(["off", "offline"]).default("off"),
    /** Fitted policy artifact. Absent means the router has nothing to serve. */
    POLICY_PATH: optionalPath,
  })
  .superRefine((env, ctx) => {
    // A router switched on with nothing to route by would silently serve
    // safe_default forever and look like it was working.
    if (env.ROUTER_MODE !== "off" && env.POLICY_PATH === undefined) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["POLICY_PATH"],
        message: `ROUTER_MODE is "${env.ROUTER_MODE}" but POLICY_PATH is not set; the router would have no policy to serve`,
      });
    }

    if (env.RETRY_MAX_DELAY_MS < env.RETRY_BASE_DELAY_MS) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["RETRY_MAX_DELAY_MS"],
        message: "must be >= RETRY_BASE_DELAY_MS",
      });
    }
    if (env.TTFT_TIMEOUT_MS > env.REQUEST_TIMEOUT_MS) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["TTFT_TIMEOUT_MS"],
        message:
          "must be <= REQUEST_TIMEOUT_MS; a first-token deadline longer than the whole-request deadline can never fire",
      });
    }

    // A gateway with no provider credentials can accept requests and satisfy
    // none of them. Better to refuse to start than to 500 on first traffic.
    const hasProvider =
      env.ANTHROPIC_API_KEY !== undefined ||
      env.OPENAI_API_KEY !== undefined ||
      env.GROQ_API_KEY !== undefined;
    if (!hasProvider) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          "at least one of ANTHROPIC_API_KEY, OPENAI_API_KEY, GROQ_API_KEY must be set — " +
          "a gateway with no provider credentials cannot serve any request",
      });
    }
  });

export type Env = z.infer<typeof EnvSchema>;

export class EnvValidationError extends Error {
  override readonly name = "EnvValidationError";
}

/** Names that must never appear in a log line or an error message. */
const SECRET_KEYS = new Set([
  "ANTHROPIC_API_KEY",
  "OPENAI_API_KEY",
  "GROQ_API_KEY",
  "DATABASE_URL",
  "REDIS_URL",
]);

/**
 * Validate a raw environment. Throws `EnvValidationError` listing every problem.
 * Secret values are never included in the message — only the variable name.
 */
export function loadEnv(raw: NodeJS.ProcessEnv = process.env): Env {
  const result = EnvSchema.safeParse(raw);
  if (result.success) return result.data;

  const issues = result.error.issues.map((issue) => {
    const name = issue.path[0];
    const key = typeof name === "string" ? name : "(environment)";
    const redacted = SECRET_KEYS.has(key) ? " (value redacted)" : "";
    return `  - ${key}: ${issue.message}${redacted}`;
  });

  throw new EnvValidationError(
    `environment validation failed; refusing to start:\n${issues.join("\n")}`,
  );
}
