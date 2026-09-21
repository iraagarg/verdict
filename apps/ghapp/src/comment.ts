/**
 * The PR comment.
 *
 * ONE renderer, used by both the GitHub Action and the webhook App. Two
 * implementations of "what does this eval mean" would eventually disagree, and
 * the disagreement would be invisible — both comments look plausible. This is
 * the same reasoning that put replay behind the gateway (D-024).
 *
 * The comment leads with the verdict rather than the delta, because a delta
 * without its interval invites exactly the reading the four-verdict design
 * exists to prevent: a 0.0pp difference on 20 items is not "no change".
 */
export type Outcome = "REGRESSION" | "IMPROVEMENT" | "EQUIVALENT" | "INCONCLUSIVE";

export interface EvalSummary {
  baseline: string;
  candidate: string;
  outcome: Outcome;
  effect: number;
  ciLow: number;
  ciHigh: number;
  margin: number;
  n: number;
  nDiscordant: number;
  mcnemarP: number;
  baselineRate: number;
  candidateRate: number;
  canDemonstrateEquivalence: boolean;
  /** Mean USD per request under each arm, for the projected cost change. */
  baselineCostUsd: number;
  candidateCostUsd: number;
  /** What this PR's eval run itself cost, and the cap it ran under. */
  runCostUsd: number;
  runCapUsd: number;
  sampled: number;
  corpusTotal: number;
  /** Set when the run stopped early. A truncated run must never read as complete. */
  aborted?: string | undefined;
}

const HEADLINE: Record<Outcome, string> = {
  REGRESSION: "🔴 **Regression** — quality is worse by an amount that matters",
  IMPROVEMENT: "🟢 **Improvement** — quality is better by an amount that matters",
  EQUIVALENT: "🔵 **Equivalent** — measured precisely, and the difference is small",
  INCONCLUSIVE: "🟡 **Inconclusive** — this run cannot tell a real difference from noise",
};

const READING: Record<Outcome, (s: EvalSummary) => string> = {
  REGRESSION: (s) =>
    `The whole interval is below −${pp(s.margin)}, so this change makes quality worse by more than the ${pp(s.margin)} margin.`,
  IMPROVEMENT: (s) =>
    `The whole interval is above +${pp(s.margin)}, so this change improves quality by more than the ${pp(s.margin)} margin.`,
  EQUIVALENT: (s) =>
    `The whole interval fits inside ±${pp(s.margin)}. This is evidence the two are close enough — not merely a failure to find a difference.`,
  INCONCLUSIVE: (s) =>
    `The interval (±${pp((s.ciHigh - s.ciLow) / 2)}) is wider than the ±${pp(s.margin)} margin, so it straddles a decision boundary. **This does not mean "no change"** — it means this sample could not resolve one. It needs more items, not a different conclusion.`,
};

function pp(v: number): string {
  return `${(v * 100).toFixed(2)}pp`;
}
function signed(v: number): string {
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}pp`;
}
function usd(v: number): string {
  return v === 0 ? "$0" : v < 0.01 ? `$${v.toFixed(6)}` : `$${v.toFixed(4)}`;
}

export const MARKER = "<!-- verdict-eval-comment -->";

export function renderComment(s: EvalSummary): string {
  const costDelta = s.candidateCostUsd - s.baselineCostUsd;
  const costPct = s.baselineCostUsd > 0 ? (costDelta / s.baselineCostUsd) * 100 : Number.NaN;

  const lines = [
    MARKER,
    "## Verdict — prompt evaluation",
    "",
    HEADLINE[s.outcome],
    "",
    READING[s.outcome](s),
    "",
    "### Quality",
    "",
    "| | |",
    "|---|---|",
    `| Comparison | \`${s.baseline}\` → \`${s.candidate}\` |`,
    `| Effect | **${signed(s.effect)}** |`,
    `| 95% CI | ${signed(s.ciLow)} … ${signed(s.ciHigh)} |`,
    `| Margin | ±${pp(s.margin)} |`,
    `| Pass rate | ${(s.baselineRate * 100).toFixed(1)}% → ${(s.candidateRate * 100).toFixed(1)}% |`,
    `| Sample | ${s.n} items (${s.nDiscordant} discordant) |`,
    `| McNemar | p = ${s.mcnemarP.toFixed(6)} |`,
    "",
    "### Projected cost",
    "",
    "| | |",
    "|---|---|",
    `| Per request, before | ${usd(s.baselineCostUsd)} |`,
    `| Per request, after | ${usd(s.candidateCostUsd)} |`,
    // Sign both halves or neither. "$0.000860 (-62.3%)" reads as a cost
    // INCREASE to anyone skimming the dollar figure.
    `| Change | ${costDelta >= 0 ? "+" : "−"}${usd(Math.abs(costDelta))}${
      Number.isFinite(costPct) ? ` (${costPct >= 0 ? "+" : ""}${costPct.toFixed(1)}%)` : ""
    } |`,
    "",
    "<details><summary>How this was run</summary>",
    "",
    `- Sampled **${s.sampled}** of ${s.corpusTotal} corpus items — a sample, so treat it as a screen, not a full evaluation.`,
    `- This run cost **${usd(s.runCostUsd)}** against a **${usd(s.runCapUsd)}** cap.`,
    `- ${s.canDemonstrateEquivalence ? "The interval is narrow enough to demonstrate equivalence if the effect were small." : "**The interval is wider than the margin**, so this run could never have returned `EQUIVALENT`, whatever the result."}`,
    "",
    "</details>",
  ];

  if (s.aborted) {
    lines.splice(
      4,
      0,
      `> ⚠️ **This run did not finish: ${s.aborted}**`,
      "> The numbers below are from a partial sample and must not be read as a complete evaluation.",
      "",
    );
  }

  return lines.join("\n");
}
