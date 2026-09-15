import { describe, expect, it } from "vitest";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PolicyError, assertServable, loadPolicy } from "./policy.js";

const VALID = {
  policy_version: 1,
  created_at: "2026-09-15T00:00:00+00:00",
  git_sha: "abc123",
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
      reason: "cleared the floor",
    },
  ],
  cascade: null,
};

function write(policy: unknown): string {
  const dir = mkdtempSync(join(tmpdir(), "verdict-policy-"));
  const path = join(dir, "policy.json");
  writeFileSync(path, JSON.stringify(policy));
  return path;
}

describe("loadPolicy", () => {
  it("loads a valid policy", () => {
    const policy = loadPolicy(write(VALID));
    expect(policy.safe_default).toBe("claude-opus-5");
    expect(policy.routes).toHaveLength(1);
  });

  it("reports a missing file clearly", () => {
    expect(() => loadPolicy("/nonexistent/policy.json")).toThrow(/cannot read policy/);
  });

  it("reports malformed JSON clearly", () => {
    const dir = mkdtempSync(join(tmpdir(), "verdict-policy-"));
    const path = join(dir, "policy.json");
    writeFileSync(path, "{not json");
    expect(() => loadPolicy(path)).toThrow(/not valid JSON/);
  });

  it("rejects an unknown field rather than ignoring it", () => {
    expect(() => loadPolicy(write({ ...VALID, surprise: true }))).toThrow(PolicyError);
  });

  it("rejects a route with no assigned model", () => {
    const bad = { ...VALID, routes: [{ ...VALID.routes[0], assigned_model: "" }] };
    expect(() => loadPolicy(write(bad))).toThrow(PolicyError);
  });

  it("rejects an out-of-range cascade threshold", () => {
    const bad = {
      ...VALID,
      cascade: {
        enabled: false,
        cheap_model: "a",
        strong_model: "b",
        threshold: 1.5,
        k_samples: 3,
        held_out_quality: 1,
        held_out_cost_ratio: 1,
        held_out_n: 10,
      },
    };
    expect(() => loadPolicy(write(bad))).toThrow(PolicyError);
  });

  it("rejects an unsupported policy version", () => {
    expect(() => loadPolicy(write({ ...VALID, policy_version: 2 }))).toThrow(PolicyError);
  });
});

describe("assertServable", () => {
  it("passes when every routed model is serviceable", () => {
    const policy = loadPolicy(write(VALID));
    expect(() => assertServable(policy, () => true)).not.toThrow();
  });

  it("fails at boot when a routed model has no provider key", () => {
    // Otherwise every request on that route 503s at runtime, one at a time.
    const policy = loadPolicy(write(VALID));
    expect(() => assertServable(policy, (m) => m !== "claude-haiku-4-5")).toThrow(
      /claude-haiku-4-5/,
    );
  });

  it("fails when even the safe default is unserviceable", () => {
    const policy = loadPolicy(write(VALID));
    expect(() => assertServable(policy, () => false)).toThrow(/claude-opus-5/);
  });
});
