/**
 * Model ladder + pricing configuration.
 *
 * This module is the single source of truth for what a model costs and what a
 * model can do. Two rules it exists to enforce:
 *
 *   1. No price without provenance. Every pricing block carries `verified_at`
 *      and `source`; a price that cannot say where it came from fails
 *      validation and the process refuses to start. Non-negotiable #1 applies
 *      to the numbers on a resume, so it applies here.
 *
 *   2. The ladder is not uniform. Claude Haiku 4.5 accepts `temperature` and
 *      uses `budget_tokens` thinking; Claude Sonnet 5 and Opus 5 reject
 *      `temperature` with an HTTP 400 and require adaptive thinking. Provider
 *      adapters read `capabilities` to normalise this so that nothing above the
 *      adapter layer knows the difference. See DESIGN.md §6.4 and DECISIONS.md D-010.
 */
import { readFileSync } from "node:fs";
import { parse as parseYaml } from "yaml";
import { z } from "zod";

export class ModelConfigError extends Error {
  override readonly name = "ModelConfigError";
}

/**
 * A price in USD per million tokens, constrained to an exact multiple of
 * $0.001/MTok.
 *
 * The cost meter converts each price to an integer number of nano-USD per token
 * (`price * 1000`) so that token accounting is exact integer arithmetic and
 * never accumulates floating-point error across a long stream. That conversion
 * is only lossless if the price has at most three decimal places. Every
 * published rate we use satisfies this ($0.075, $1.25, $12.50), so enforcing it
 * here turns a silent precision bug into a boot failure.
 */
const price = z
  .number()
  .nonnegative()
  .refine((v) => Math.abs(v * 1000 - Math.round(v * 1000)) < 1e-9, {
    message:
      "must be an exact multiple of 0.001 USD/MTok so cost can be computed in integer nano-USD",
  });

/** USD per million tokens. */
const Pricing = z
  .object({
    input: price,
    output: price,
    /** `null` means "not yet verified" — NOT zero. Cost code must fail on null. */
    cache_read: price.nullable(),
    cache_write: price.nullable(),
    verified_at: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "verified_at must be YYYY-MM-DD"),
    source: z.string().url("source must be a URL pointing at official pricing documentation"),
  })
  .strict();

const Capabilities = z
  .object({
    /** `temperature` / `top_p` / `top_k`. False on Claude Opus 5 and Sonnet 5 — they return 400. */
    supports_sampling_params: z.boolean(),
    /** `output_config.effort`. False on Claude Haiku 4.5 — it errors. */
    supports_effort: z.boolean(),
    thinking_mode: z.enum(["adaptive", "budget_tokens", "none"]),
    supports_prefill: z.boolean(),
  })
  .strict();

export const RUNGS = ["cheap", "mid", "strong"] as const;
export type Rung = (typeof RUNGS)[number];

const ModelEntry = z
  .object({
    provider: z.enum(["anthropic", "openai", "groq"]),
    rung: z.enum(RUNGS),
    context_window: z.number().int().positive(),
    max_output_tokens: z.number().int().positive(),
    pricing: Pricing,
    capabilities: Capabilities,
  })
  .strict();

/**
 * Current Claude model IDs carry no date suffix. `claude-haiku-4-5-20251001`
 * is not a valid ID and fails at request time, so it fails at boot instead.
 */
const DATE_SUFFIXED = /-\d{8}$/;

const ModelConfig = z
  .object({
    version: z.literal(1),
    embedding: z
      .object({
        /** `dimensions` is migration-breaking (DECISIONS.md D-015). */
        model: z.string().min(1).nullable(),
        dimensions: z.number().int().positive().nullable(),
        provider: z.enum(["anthropic", "openai", "groq"]).optional(),
        pricing: Pricing.optional(),
      })
      .strict(),
    safe_default: z.string().min(1),
    reference_model: z.string().min(1),
    models: z.record(z.string().min(1), ModelEntry),
  })
  .strict()
  .superRefine((cfg, ctx) => {
    const ids = Object.keys(cfg.models);

    if (ids.length === 0) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "models must not be empty" });
    }

    for (const id of ids) {
      if (cfg.models[id]?.provider === "anthropic" && DATE_SUFFIXED.test(id)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["models", id],
          message: `"${id}" has a date suffix. Current Claude model IDs do not — use "${id.replace(DATE_SUFFIXED, "")}".`,
        });
      }
    }

    for (const field of ["safe_default", "reference_model"] as const) {
      const id = cfg[field];
      if (!Object.hasOwn(cfg.models, id)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: [field],
          message: `${field} "${id}" is not defined in models`,
        });
      }
    }

    // The router must fail toward quality: every ambiguous path resolves to
    // safe_default, so safe_default being a cheap rung would silently invert
    // the safety property. DESIGN.md §8.2.
    const fallback = cfg.models[cfg.safe_default];
    if (fallback && fallback.rung !== "strong") {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["safe_default"],
        message: `safe_default must be a "strong" rung (got "${fallback.rung}") — the router falls back toward quality, never toward cost`,
      });
    }

    // Embedding model and dimensions are decided together or not at all.
    const { model, dimensions } = cfg.embedding;
    if ((model === null) !== (dimensions === null)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["embedding"],
        message: "embedding.model and embedding.dimensions must both be set or both be null",
      });
    }
  });

export type ModelConfig = z.infer<typeof ModelConfig>;
export type ModelEntry = z.infer<typeof ModelEntry>;
export type Pricing = z.infer<typeof Pricing>;
export type Capabilities = z.infer<typeof Capabilities>;

/** Parse and validate YAML text. Throws `ModelConfigError` with every issue listed. */
export function parseModelConfig(yamlText: string): ModelConfig {
  let raw: unknown;
  try {
    raw = parseYaml(yamlText);
  } catch (err) {
    throw new ModelConfigError(`models.yaml is not valid YAML: ${(err as Error).message}`);
  }

  const result = ModelConfig.safeParse(raw);
  if (!result.success) {
    const issues = result.error.issues
      .map((i) => `  - ${i.path.join(".") || "(root)"}: ${i.message}`)
      .join("\n");
    throw new ModelConfigError(`models.yaml failed validation:\n${issues}`);
  }
  return result.data;
}

/** Read, parse and validate the config at `path`. */
export function loadModelConfig(path: string): ModelConfig {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch (err) {
    throw new ModelConfigError(`cannot read models.yaml at ${path}: ${(err as Error).message}`);
  }
  return parseModelConfig(text);
}

/** Look up a model, throwing rather than returning undefined. */
export function getModel(cfg: ModelConfig, id: string): ModelEntry {
  const entry = cfg.models[id];
  if (!entry) {
    throw new ModelConfigError(
      `unknown model "${id}"; configured models are: ${Object.keys(cfg.models).join(", ")}`,
    );
  }
  return entry;
}

/** Models at a given rung, cheapest input price first. */
export function modelsAtRung(cfg: ModelConfig, rung: Rung): string[] {
  return Object.entries(cfg.models)
    .filter(([, m]) => m.rung === rung)
    .sort(([, a], [, b]) => a.pricing.input - b.pricing.input)
    .map(([id]) => id);
}

/**
 * Integer nano-USD per token for a price expressed in USD per million tokens.
 *
 * $1.00/MTok = 1e-6 USD/token = 1000 nano-USD/token. The schema guarantees the
 * input has at most three decimals, so this is exact.
 */
export function nanoUsdPerToken(usdPerMillionTokens: number): number {
  return Math.round(usdPerMillionTokens * 1000);
}

/** Convert integer nano-USD to USD, rounded to the 8 decimals the schema stores. */
export function nanoUsdToUsd(nanoUsd: number): number {
  return Math.round(nanoUsd / 10) / 1e8;
}
