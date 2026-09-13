import { describe, expect, it } from "vitest";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import {
  getModel,
  loadModelConfig,
  modelsAtRung,
  ModelConfigError,
  parseModelConfig,
} from "./models.js";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");

/** A minimal valid config. Tests mutate a clone of this to isolate one failure at a time. */
const VALID = `
version: 1
embedding:
  model: null
  dimensions: null
safe_default: claude-opus-5
reference_model: claude-opus-5
models:
  claude-haiku-4-5:
    provider: anthropic
    rung: cheap
    context_window: 200000
    max_output_tokens: 64000
    pricing:
      input: 1.00
      output: 5.00
      cache_read: null
      cache_write: null
      verified_at: "2026-09-13"
      source: "https://www.anthropic.com/pricing"
    capabilities:
      supports_sampling_params: true
      supports_effort: false
      thinking_mode: budget_tokens
      supports_prefill: true
  claude-opus-5:
    provider: anthropic
    rung: strong
    context_window: 1000000
    max_output_tokens: 128000
    pricing:
      input: 5.00
      output: 25.00
      cache_read: null
      cache_write: null
      verified_at: "2026-09-13"
      source: "https://www.anthropic.com/pricing"
    capabilities:
      supports_sampling_params: false
      supports_effort: true
      thinking_mode: adaptive
      supports_prefill: false
`;

describe("parseModelConfig", () => {
  it("accepts a well-formed config", () => {
    const cfg = parseModelConfig(VALID);
    expect(Object.keys(cfg.models)).toEqual(["claude-haiku-4-5", "claude-opus-5"]);
    expect(cfg.safe_default).toBe("claude-opus-5");
  });

  it("rejects a price with no verified_at", () => {
    const bad = VALID.replaceAll('      verified_at: "2026-09-13"\n', "");
    expect(() => parseModelConfig(bad)).toThrow(ModelConfigError);
    expect(() => parseModelConfig(bad)).toThrow(/verified_at/);
  });

  it("rejects a price with no source", () => {
    const bad = VALID.replaceAll('      source: "https://www.anthropic.com/pricing"\n', "");
    expect(() => parseModelConfig(bad)).toThrow(/source/);
  });

  it("rejects a source that is not a URL", () => {
    const bad = VALID.replaceAll('"https://www.anthropic.com/pricing"', '"I asked a chatbot"');
    expect(() => parseModelConfig(bad)).toThrow(/source/);
  });

  it("rejects a negative price", () => {
    expect(() => parseModelConfig(VALID.replace("input: 1.00", "input: -1.00"))).toThrow(
      ModelConfigError,
    );
  });

  it("rejects unknown keys so typos cannot be silently ignored", () => {
    const bad = VALID.replace("version: 1", "version: 1\nunexpected_key: true");
    expect(() => parseModelConfig(bad)).toThrow(/unexpected_key/);
  });

  it("rejects a date-suffixed Claude model id", () => {
    // The project brief originally specified claude-haiku-4-5-20251001, which
    // is not a valid ID. Catching it at boot beats catching it at request time.
    const bad = VALID.replace(/claude-haiku-4-5/g, "claude-haiku-4-5-20251001");
    expect(() => parseModelConfig(bad)).toThrow(/date suffix/);
    expect(() => parseModelConfig(bad)).toThrow(/claude-haiku-4-5/);
  });

  it("rejects a safe_default that is not a defined model", () => {
    const bad = VALID.replace("safe_default: claude-opus-5", "safe_default: gpt-imaginary");
    expect(() => parseModelConfig(bad)).toThrow(/safe_default .* is not defined/);
  });

  it("rejects a reference_model that is not a defined model", () => {
    const bad = VALID.replace("reference_model: claude-opus-5", "reference_model: nope");
    expect(() => parseModelConfig(bad)).toThrow(/reference_model .* is not defined/);
  });

  it("rejects a cheap safe_default — the router must fail toward quality", () => {
    const bad = VALID.replace("safe_default: claude-opus-5", "safe_default: claude-haiku-4-5");
    expect(() => parseModelConfig(bad)).toThrow(/must be a "strong" rung/);
  });

  it("rejects a half-configured embedding block", () => {
    const bad = VALID.replace("  dimensions: null", "  dimensions: 1536");
    expect(() => parseModelConfig(bad)).toThrow(/both be set or both be null/);
  });

  it("distinguishes null cache pricing from zero", () => {
    const cfg = parseModelConfig(VALID);
    // null means "unverified". Cost code must fail on it rather than assume free.
    expect(getModel(cfg, "claude-opus-5").pricing.cache_read).toBeNull();
    expect(getModel(cfg, "claude-opus-5").pricing.cache_read).not.toBe(0);
  });

  it("reports every validation issue at once, not just the first", () => {
    const bad = VALID.replace("input: 1.00", "input: -1.00").replace(
      "safe_default: claude-opus-5",
      "safe_default: missing-model",
    );
    let message = "";
    try {
      parseModelConfig(bad);
    } catch (err) {
      message = (err as Error).message;
    }
    expect(message).toMatch(/input/);
    expect(message).toMatch(/safe_default/);
  });

  it("rejects YAML that is not parseable", () => {
    expect(() => parseModelConfig("version: 1\n  bad: [indent")).toThrow(/not valid YAML/);
  });
});

describe("getModel", () => {
  it("throws a helpful error listing known models", () => {
    const cfg = parseModelConfig(VALID);
    expect(() => getModel(cfg, "claude-sonnet-5")).toThrow(/unknown model "claude-sonnet-5"/);
    expect(() => getModel(cfg, "claude-sonnet-5")).toThrow(/claude-haiku-4-5/);
  });
});

describe("modelsAtRung", () => {
  it("returns models at a rung ordered by input price", () => {
    const cfg = parseModelConfig(VALID);
    expect(modelsAtRung(cfg, "cheap")).toEqual(["claude-haiku-4-5"]);
    expect(modelsAtRung(cfg, "strong")).toEqual(["claude-opus-5"]);
    expect(modelsAtRung(cfg, "mid")).toEqual([]);
  });
});

describe("the committed config/models.yaml", () => {
  // Guards against the real config drifting away from the schema.
  it("passes validation", () => {
    const cfg = loadModelConfig(resolve(REPO_ROOT, "config/models.yaml"));
    expect(cfg.version).toBe(1);
    expect(cfg.models["claude-opus-5"]).toBeDefined();
  });

  it("prices every model with documented provenance", () => {
    const cfg = loadModelConfig(resolve(REPO_ROOT, "config/models.yaml"));
    for (const [id, model] of Object.entries(cfg.models)) {
      expect(model.pricing.source, `${id} must cite a pricing source`).toMatch(/^https:\/\//);
      expect(model.pricing.verified_at, `${id} must record when its price was checked`).toMatch(
        /^\d{4}-\d{2}-\d{2}$/,
      );
    }
  });

  it("defines all three rungs", () => {
    const cfg = loadModelConfig(resolve(REPO_ROOT, "config/models.yaml"));
    expect(modelsAtRung(cfg, "cheap").length).toBeGreaterThan(0);
    expect(modelsAtRung(cfg, "mid").length).toBeGreaterThan(0);
    expect(modelsAtRung(cfg, "strong").length).toBeGreaterThan(0);
  });

  it("orders the ladder so that cheap is actually cheaper than strong", () => {
    const cfg = loadModelConfig(resolve(REPO_ROOT, "config/models.yaml"));
    const priceOf = (id: string) => getModel(cfg, id).pricing.input;
    const cheapest = Math.min(...modelsAtRung(cfg, "cheap").map(priceOf));
    const strongest = Math.min(...modelsAtRung(cfg, "strong").map(priceOf));
    expect(cheapest).toBeLessThan(strongest);
  });

  it("throws a clear error when the file is missing", () => {
    expect(() => loadModelConfig("/nonexistent/models.yaml")).toThrow(/cannot read models.yaml/);
  });
});
