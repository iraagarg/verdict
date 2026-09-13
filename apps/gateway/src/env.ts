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
    REDIS_URL: z
      .string()
      .url()
      .refine((v) => v.startsWith("redis://") || v.startsWith("rediss://"), {
        message: "must be a redis:// or rediss:// URL",
      }),

    ANTHROPIC_API_KEY: apiKey,
    OPENAI_API_KEY: apiKey,
    GROQ_API_KEY: apiKey,

    COST_CAP_USD_PER_DAY: z.coerce.number().positive(),
  })
  .superRefine((env, ctx) => {
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
