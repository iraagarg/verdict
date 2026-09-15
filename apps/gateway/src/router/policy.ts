/**
 * The fitted routing policy, as the gateway sees it.
 *
 * Loaded from the artifact `evald` writes. Validated with Zod on load, because
 * a malformed policy must fail at boot rather than mis-route traffic at request
 * time — the same rule the env schema follows (non-negotiable #6).
 *
 * The gateway deliberately reads `policy.json` and NOT `pareto.json`. The Pareto
 * artifact is the measurement, full of numbers the serving path has no business
 * knowing; the policy is the decision, and it is small and boring on purpose.
 */
import { readFileSync } from "node:fs";
import { z } from "zod";

export const RouteEntry = z
  .object({
    route_key: z.string().min(1),
    assigned_model: z.string().min(1),
    n_items: z.number().int().nonnegative(),
    quality: z.number(),
    ci_low: z.number(),
    ci_high: z.number(),
    floor: z.number(),
    reason: z.string(),
  })
  .strict();

export const CascadeEntry = z
  .object({
    enabled: z.boolean(),
    cheap_model: z.string().min(1),
    strong_model: z.string().min(1),
    threshold: z.number().min(0).max(1),
    k_samples: z.number().int().positive(),
    held_out_quality: z.number(),
    held_out_cost_ratio: z.number(),
    held_out_n: z.number().int().nonnegative(),
  })
  .strict();

export const PolicyArtifact = z
  .object({
    policy_version: z.literal(1),
    created_at: z.string(),
    git_sha: z.string(),
    corpus_sha256: z.string(),
    safe_default: z.string().min(1),
    floor: z.number(),
    margin: z.number(),
    routes: z.array(RouteEntry),
    cascade: CascadeEntry.nullable().default(null),
  })
  .strict();

export type PolicyArtifact = z.infer<typeof PolicyArtifact>;
export type RouteEntry = z.infer<typeof RouteEntry>;

export class PolicyError extends Error {
  override readonly name = "PolicyError";
}

/** Read and validate a policy. Throws rather than returning a partial policy. */
export function loadPolicy(path: string): PolicyArtifact {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch (err) {
    throw new PolicyError(`cannot read policy at ${path}: ${(err as Error).message}`);
  }

  let raw: unknown;
  try {
    raw = JSON.parse(text) as unknown;
  } catch (err) {
    throw new PolicyError(`policy at ${path} is not valid JSON: ${(err as Error).message}`);
  }

  const result = PolicyArtifact.safeParse(raw);
  if (!result.success) {
    const issues = result.error.issues
      .map((i) => `  - ${i.path.join(".") || "(root)"}: ${i.message}`)
      .join("\n");
    throw new PolicyError(`policy at ${path} failed validation:\n${issues}`);
  }
  return result.data;
}

/**
 * A policy that names models the gateway cannot serve would route traffic into
 * a 503 on every request. Checked once at boot.
 */
export function assertServable(
  policy: PolicyArtifact,
  isServable: (model: string) => boolean,
): void {
  const missing = new Set<string>();
  if (!isServable(policy.safe_default)) missing.add(policy.safe_default);
  for (const route of policy.routes) {
    if (!isServable(route.assigned_model)) missing.add(route.assigned_model);
  }
  if (missing.size > 0) {
    throw new PolicyError(
      `policy routes to models this gateway cannot serve: ${[...missing].sort().join(", ")}. ` +
        `Configure the relevant provider API keys, or refit the policy against the models you have.`,
    );
  }
}
