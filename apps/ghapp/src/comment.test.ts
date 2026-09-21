import { describe, expect, it } from "vitest";
import { MARKER, renderComment, type EvalSummary } from "./comment.js";

const base: EvalSummary = {
  baseline: "prompts/judge@main",
  candidate: "prompts/judge@pr-42",
  outcome: "INCONCLUSIVE",
  effect: 0,
  ciLow: -0.1,
  ciHigh: 0.1,
  margin: 0.03,
  n: 60,
  nDiscordant: 8,
  mcnemarP: 1,
  baselineRate: 0.8167,
  candidateRate: 0.8167,
  canDemonstrateEquivalence: false,
  baselineCostUsd: 0.00138,
  candidateCostUsd: 0.00052,
  runCostUsd: 0.19,
  runCapUsd: 1,
  sampled: 60,
  corpusTotal: 1500,
};

const s = (over: Partial<EvalSummary>): string => renderComment({ ...base, ...over });

describe("renderComment", () => {
  it("carries a marker so the bot can update its own comment", () => {
    // Without this the bot posts a new comment on every push.
    expect(renderComment(base).startsWith(MARKER)).toBe(true);
  });

  it("leads with the verdict, not the delta", () => {
    // A delta without its interval invites exactly the misreading the four
    // verdicts exist to prevent.
    const body = renderComment(base);
    expect(body.indexOf("Inconclusive")).toBeLessThan(body.indexOf("Effect"));
  });

  it("states explicitly that inconclusive is not 'no change'", () => {
    expect(s({ outcome: "INCONCLUSIVE" })).toContain('does not mean "no change"');
  });

  it("distinguishes equivalent from inconclusive in words", () => {
    const equivalent = s({ outcome: "EQUIVALENT", ciLow: -0.01, ciHigh: 0.01 });
    expect(equivalent).toContain("evidence the two are close enough");
    expect(equivalent).not.toContain('does not mean "no change"');
  });

  it.each(["REGRESSION", "IMPROVEMENT", "EQUIVALENT", "INCONCLUSIVE"] as const)(
    "renders %s with its own reading",
    (outcome) => {
      expect(s({ outcome })).toContain(
        outcome === "REGRESSION" ? "Regression" : outcome[0] + outcome.slice(1).toLowerCase(),
      );
    },
  );

  it("always shows the confidence interval next to the effect", () => {
    const body = renderComment(base);
    expect(body).toContain("95% CI");
    expect(body).toContain("+0.00pp");
    expect(body).toMatch(/-10\.00pp … \+10\.00pp/);
  });

  it("reports the projected cost change in both dollars and percent", () => {
    const body = renderComment(base);
    expect(body).toContain("Projected cost");
    expect(body).toContain("$0.001380");
    expect(body).toContain("$0.000520");
    expect(body).toMatch(/-62\.3%/);
  });

  it("signs the dollar change, not just the percentage", () => {
    // "$0.000860 (-62.3%)" reads as an increase to anyone skimming the dollars.
    const cheaper = renderComment(base);
    expect(cheaper).toMatch(/−\$0\.000860 \(-62\.3%\)/);
    const dearer = s({ baselineCostUsd: 0.0005, candidateCostUsd: 0.0015 });
    expect(dearer).toMatch(/\+\$0\.001000 \(\+200\.0%\)/);
  });

  it("handles a zero baseline cost without printing NaN", () => {
    expect(s({ baselineCostUsd: 0 })).not.toContain("NaN");
  });

  it("says what the run itself cost and under what cap", () => {
    // A PR bot that spends money should say how much, every time.
    const body = renderComment(base);
    expect(body).toContain("$0.1900");
    expect(body).toContain("$1.0000");
  });

  it("says the sample is a sample", () => {
    expect(renderComment(base)).toContain("60** of 1500");
    expect(renderComment(base)).toContain("a screen, not a full evaluation");
  });

  it("warns when the interval was too wide to ever show equivalence", () => {
    expect(s({ canDemonstrateEquivalence: false })).toContain("could never have returned");
  });

  it("does not warn when the run could have shown equivalence", () => {
    expect(s({ canDemonstrateEquivalence: true })).not.toContain("could never have returned");
  });

  it("puts an abort warning ABOVE the numbers", () => {
    // A truncated run must never be mistaken for a complete one, and a footnote
    // at the bottom of a long comment will not be read.
    const body = s({ aborted: "spend cap reached at $1.00" });
    expect(body).toContain("did not finish");
    expect(body.indexOf("did not finish")).toBeLessThan(body.indexOf("### Quality"));
  });

  it("omits the abort warning on a complete run", () => {
    expect(renderComment(base)).not.toContain("did not finish");
  });

  it("produces valid markdown tables", () => {
    for (const line of renderComment(base).split("\n")) {
      if (line.startsWith("|") && !line.includes("---")) {
        expect(line.endsWith("|")).toBe(true);
      }
    }
  });
});
