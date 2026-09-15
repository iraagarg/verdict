/**
 * Model selection.
 *
 * Every test here is really one assertion: ambiguity resolves toward quality.
 * A router that silently picks a cheap model when it is unsure is the specific
 * failure this project exists to prevent, and it would be invisible in the
 * output — the response looks identical either way.
 */
import { describe, expect, it } from "vitest";
import { AUTO_MODEL, parseModeOverride, selectModel } from "./select.js";
import type { PolicyArtifact } from "./policy.js";

const POLICY: PolicyArtifact = {
  policy_version: 1,
  created_at: "2026-09-15T00:00:00+00:00",
  git_sha: "abc",
  corpus_sha256: "f".repeat(64),
  safe_default: "claude-opus-5",
  floor: 0.9,
  margin: 0.03,
  routes: [
    {
      route_key: "multiple_choice",
      assigned_model: "claude-haiku-4-5",
      n_items: 300,
      quality: 0.96,
      ci_low: 0.93,
      ci_high: 0.98,
      floor: 0.9,
      reason: "cheapest rung whose CI lower bound cleared the floor",
    },
  ],
  cascade: null,
};

const base = {
  requestedModel: AUTO_MODEL,
  routeHint: undefined as string | undefined,
  mode: "offline" as const,
  policy: POLICY,
  fallbackModel: "claude-opus-5",
};

describe("selectModel", () => {
  it("honours an explicitly named model and never overrides it", () => {
    // A router that second-guesses an explicit choice makes the gateway
    // impossible to debug.
    const s = selectModel({
      ...base,
      requestedModel: "claude-haiku-4-5",
      routeHint: "multiple_choice",
    });
    expect(s.model).toBe("claude-haiku-4-5");
    expect(s.reason).toBe("explicit_model");
  });

  it("routes an auto request to the policy's assigned model", () => {
    const s = selectModel({ ...base, routeHint: "multiple_choice" });
    expect(s.model).toBe("claude-haiku-4-5");
    expect(s.routeKey).toBe("multiple_choice");
    expect(s.reason).toBe("policy_route");
  });

  it("serves the safe default when the router is off", () => {
    const s = selectModel({ ...base, mode: "off", routeHint: "multiple_choice" });
    expect(s.model).toBe("claude-opus-5");
    expect(s.reason).toBe("router_off");
  });

  it("serves the safe default when no policy is loaded", () => {
    const s = selectModel({ ...base, policy: undefined, routeHint: "multiple_choice" });
    expect(s.model).toBe("claude-opus-5");
    expect(s.reason).toBe("no_policy");
  });

  it("serves the safe default when no route hint is supplied", () => {
    // Guessing the route would apply a guarantee fitted for different traffic.
    const s = selectModel({ ...base, routeHint: undefined });
    expect(s.model).toBe("claude-opus-5");
    expect(s.reason).toBe("no_route_hint");
  });

  it("treats a blank route hint as absent", () => {
    expect(selectModel({ ...base, routeHint: "   " }).reason).toBe("no_route_hint");
  });

  it("serves the safe default for a route the policy never saw", () => {
    // Traffic outside the fitted distribution does not get the discount.
    const s = selectModel({ ...base, routeHint: "astrology" });
    expect(s.model).toBe("claude-opus-5");
    expect(s.reason).toBe("unknown_route");
    expect(s.routeKey).toBe("astrology");
  });

  it("never returns a cheap model without positive evidence", () => {
    // The property, stated directly: every ambiguous path yields safe_default.
    const ambiguous = [
      { ...base, mode: "off" as const },
      { ...base, policy: undefined },
      { ...base, routeHint: undefined },
      { ...base, routeHint: "never-fitted" },
    ];
    for (const opts of ambiguous) {
      expect(selectModel(opts).model).toBe("claude-opus-5");
    }
  });

  it("falls back to the configured default when there is no policy at all", () => {
    const s = selectModel({ ...base, policy: undefined, fallbackModel: "claude-sonnet-5" });
    expect(s.model).toBe("claude-sonnet-5");
  });
});

describe("parseModeOverride", () => {
  it("accepts the supported modes", () => {
    expect(parseModeOverride("off")).toBe("off");
    expect(parseModeOverride("offline")).toBe("offline");
  });

  it("is case and whitespace insensitive", () => {
    expect(parseModeOverride("  OFFLINE  ")).toBe("offline");
  });

  it("ignores an unknown value rather than failing the request", () => {
    // A typo'd header should not 400 a request that is otherwise fine; the
    // configured mode simply stands.
    expect(parseModeOverride("cascade")).toBeUndefined();
    expect(parseModeOverride("")).toBeUndefined();
    expect(parseModeOverride(undefined)).toBeUndefined();
    expect(parseModeOverride(["a", "b"])).toBeUndefined();
  });
});
