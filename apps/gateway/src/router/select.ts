/**
 * Model selection.
 *
 * One rule governs everything here: **ambiguity resolves toward quality**. No
 * policy, no route hint, an unknown route, a disabled router — every one of
 * those serves `safe_default`, which the policy schema guarantees is a strong
 * rung. Cost optimisation only happens where there is positive evidence it is
 * safe (DESIGN.md §8.2).
 *
 * Route keys come from an explicit `x-verdict-route` header rather than being
 * inferred from the prompt. Inference needs prompt embeddings, and the embedding
 * model is still unchosen (DECISIONS.md D-039). Guessing a route would silently
 * apply a guarantee that was fitted for different traffic, so an absent hint
 * falls back to the strong rung instead.
 */
import type { PolicyArtifact } from "./policy.js";

export const ROUTER_MODES = ["off", "offline"] as const;
export type RouterMode = (typeof ROUTER_MODES)[number];

/** Why a particular model was chosen. Reported to the client and on the trace. */
export type RouteReason =
  | "explicit_model"
  | "router_off"
  | "no_policy"
  | "no_route_hint"
  | "unknown_route"
  | "policy_route"
  | "stream_not_cascadable";

export interface Selection {
  model: string;
  routeKey: string | null;
  reason: RouteReason;
}

/** The sentinel that delegates model choice to Verdict. */
export const AUTO_MODEL = "verdict-auto";

export interface SelectOptions {
  requestedModel: string;
  routeHint: string | undefined;
  mode: RouterMode;
  policy: PolicyArtifact | undefined;
  /** Used only when there is no policy at all. */
  fallbackModel: string;
}

export function selectModel(opts: SelectOptions): Selection {
  // A caller who names a model gets that model. The router never overrides an
  // explicit choice; that would make the gateway unpredictable to debug.
  if (opts.requestedModel !== AUTO_MODEL) {
    return { model: opts.requestedModel, routeKey: null, reason: "explicit_model" };
  }

  const safeDefault = opts.policy?.safe_default ?? opts.fallbackModel;

  if (opts.mode === "off") {
    return { model: safeDefault, routeKey: null, reason: "router_off" };
  }
  if (!opts.policy) {
    return { model: safeDefault, routeKey: null, reason: "no_policy" };
  }
  if (opts.routeHint === undefined || opts.routeHint.trim() === "") {
    return { model: safeDefault, routeKey: null, reason: "no_route_hint" };
  }

  const key = opts.routeHint.trim();
  const route = opts.policy.routes.find((r) => r.route_key === key);
  if (!route) {
    // Traffic the policy was never fitted on. The guarantee does not cover it,
    // so it does not get the discount.
    return { model: safeDefault, routeKey: key, reason: "unknown_route" };
  }

  return { model: route.assigned_model, routeKey: key, reason: "policy_route" };
}

/** Parse the per-request override header. Unknown values are ignored, not errors. */
export function parseModeOverride(header: string | string[] | undefined): RouterMode | undefined {
  if (typeof header !== "string") return undefined;
  const value = header.trim().toLowerCase();
  return (ROUTER_MODES as readonly string[]).includes(value) ? (value as RouterMode) : undefined;
}
