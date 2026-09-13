import { describe, expect, it } from "vitest";
import { loadModelConfig, getModel, type ModelEntry } from "@verdict/shared";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { CostAccountingError, CostMeter } from "./meter.js";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const cfg = loadModelConfig(resolve(REPO_ROOT, "config/models.yaml"));

const haiku = getModel(cfg, "claude-haiku-4-5"); // $1.00 in / $5.00 out / $0.10 read / $1.25 write
const opus = getModel(cfg, "claude-opus-5"); // $5.00 in / $25.00 out
const groq = getModel(cfg, "openai/gpt-oss-20b"); // $0.075 in / $0.30 out, no cache rates

describe("CostMeter", () => {
  it("starts at zero and unconfirmed", () => {
    const s = new CostMeter("claude-haiku-4-5", haiku).snapshot();
    expect(s.costUsd).toBe(0);
    expect(s.costNanoUsd).toBe(0);
    expect(s.isFinal).toBe(false);
  });

  it("computes a plain input+output cost exactly", () => {
    const m = new CostMeter("claude-haiku-4-5", haiku);
    m.observe({ inputTokens: 1_000_000, outputTokens: 1_000_000 });
    // 1M in at $1 + 1M out at $5 = $6.00
    expect(m.snapshot().costUsd).toBe(6);
  });

  it("computes sub-cent costs without floating-point drift", () => {
    const m = new CostMeter("openai/gpt-oss-20b", groq);
    m.observe({ inputTokens: 1_234, outputTokens: 567 });
    // 1234 * 75 nano + 567 * 300 nano = 92_550 + 170_100 = 262_650 nano-USD
    expect(m.snapshot().costNanoUsd).toBe(262_650);
    expect(m.snapshot().costUsd).toBeCloseTo(0.00026265, 10);
  });

  it("accumulates exactly across many incremental observations", () => {
    // The real failure mode this guards: a long stream updating the meter
    // thousands of times. Float accumulation would drift; integers cannot.
    const m = new CostMeter("claude-opus-5", opus);
    for (let i = 1; i <= 10_000; i++) m.observe({ outputTokens: i });
    const s = m.snapshot();
    expect(s.outputTokens).toBe(10_000);
    expect(s.costNanoUsd).toBe(10_000 * 25_000); // $25/MTok = 25000 nano/token
    expect(s.costUsd).toBe(0.25);
  });

  it("treats usage as absolute, not additive", () => {
    // Anthropic sends a CUMULATIVE output_tokens on every message_delta.
    // Adding them would multiply the bill by the number of events.
    const m = new CostMeter("claude-opus-5", opus);
    m.observe({ outputTokens: 10 });
    m.observe({ outputTokens: 20 });
    m.observe({ outputTokens: 30 });
    expect(m.snapshot().outputTokens).toBe(30);
  });

  it("leaves untouched fields alone when a partial observation arrives", () => {
    const m = new CostMeter("claude-opus-5", opus);
    m.observe({ inputTokens: 500 }); // message_start
    m.observe({ outputTokens: 40 }); // message_delta carries only output
    const s = m.snapshot();
    expect(s.inputTokens).toBe(500);
    expect(s.outputTokens).toBe(40);
  });

  it("prices cache reads and writes separately from base input", () => {
    const m = new CostMeter("claude-haiku-4-5", haiku);
    m.observe({
      inputTokens: 1_000_000,
      cacheReadTokens: 1_000_000,
      cacheWriteTokens: 1_000_000,
      outputTokens: 0,
    });
    // $1.00 + $0.10 + $1.25 = $2.35
    expect(m.snapshot().costUsd).toBe(2.35);
  });

  it("does not charge for cache tokens that were never reported", () => {
    const m = new CostMeter("openai/gpt-oss-20b", groq); // has null cache rates
    m.observe({ inputTokens: 100, outputTokens: 100 });
    expect(() => m.snapshot()).not.toThrow();
  });

  it("refuses to under-report when cache tokens are billed but the rate is unverified", () => {
    // Silently treating a null price as zero would under-count real spend.
    // Non-negotiable #1 says that number must not exist rather than be wrong.
    const m = new CostMeter("openai/gpt-oss-20b", groq);
    m.observe({ inputTokens: 100, cacheReadTokens: 50 });
    expect(() => m.snapshot()).toThrow(CostAccountingError);
    expect(() => m.snapshot()).toThrow(/no verified cache_read price/);
  });

  it("reports finality only after the provider confirms", () => {
    const m = new CostMeter("claude-opus-5", opus);
    m.observe({ inputTokens: 10, outputTokens: 5 });
    expect(m.snapshot().isFinal).toBe(false);
    m.markFinal();
    expect(m.snapshot().isFinal).toBe(true);
  });

  it("is idempotent when marked final twice", () => {
    const m = new CostMeter("claude-opus-5", opus);
    m.markFinal();
    m.markFinal();
    expect(m.snapshot().isFinal).toBe(true);
  });

  it("rejects a negative or fractional token count from a provider", () => {
    const m = new CostMeter("claude-opus-5", opus);
    expect(() => m.observe({ outputTokens: -1 })).toThrow(CostAccountingError);
    expect(() => m.observe({ outputTokens: 1.5 })).toThrow(CostAccountingError);
  });

  it("ignores explicitly undefined fields", () => {
    const m = new CostMeter("claude-opus-5", opus);
    m.observe({ inputTokens: 7 });
    m.observe({ inputTokens: undefined, outputTokens: 3 });
    expect(m.snapshot().inputTokens).toBe(7);
  });

  it("matches a hand-computed Anthropic stream end to end", () => {
    // message_start: 1200 input, 340 cache read, 0 cache write
    // message_delta x3: cumulative output 10 -> 120 -> 256
    const m = new CostMeter("claude-sonnet-5", getModel(cfg, "claude-sonnet-5"));
    m.observe({ inputTokens: 1200, cacheReadTokens: 340, cacheWriteTokens: 0, outputTokens: 1 });
    m.observe({ outputTokens: 10 });
    m.observe({ outputTokens: 120 });
    m.observe({ outputTokens: 256 });
    m.markFinal();
    const s = m.snapshot();
    // 1200*2000 + 340*200 + 256*10000 = 2_400_000 + 68_000 + 2_560_000 = 5_028_000 nano
    expect(s.costNanoUsd).toBe(5_028_000);
    expect(s.costUsd).toBeCloseTo(0.005028, 10);
    expect(s.isFinal).toBe(true);
  });

  it("never mutates the caller's usage object", () => {
    const m = new CostMeter("claude-opus-5", opus);
    const input: Partial<{ outputTokens: number }> = { outputTokens: 5 };
    m.observe(input);
    expect(input).toEqual({ outputTokens: 5 });
  });

  it("returns an independent snapshot each call", () => {
    const m = new CostMeter("claude-opus-5", opus);
    m.observe({ outputTokens: 5 });
    const first = m.snapshot();
    m.observe({ outputTokens: 9 });
    expect(first.outputTokens).toBe(5);
  });

  it("works for every model in the committed config", () => {
    for (const [id] of Object.entries(cfg.models)) {
      const entry: ModelEntry = getModel(cfg, id);
      const m = new CostMeter(id, entry);
      m.observe({ inputTokens: 1000, outputTokens: 1000 });
      expect(m.snapshot().costNanoUsd, id).toBeGreaterThan(0);
    }
  });
});
