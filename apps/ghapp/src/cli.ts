#!/usr/bin/env node
/**
 * Renders the PR comment from an eval artifact, for the GitHub Action.
 *
 * The Action shells out to this rather than reimplementing the markdown, so
 * the Action and the webhook App emit byte-identical comments. Two renderers
 * would drift, and the drift would be invisible because both outputs look
 * plausible (the D-024 lesson, applied again).
 *
 *   verdict-pr-comment <verdict.json> [--cap 1.00] [--run-cost 0.19]
 */
import { readFileSync } from "node:fs";
import { renderComment, type EvalSummary, type Outcome } from "./comment.js";

function arg(name: string, fallback: number): number {
  const i = process.argv.indexOf(`--${name}`);
  if (i === -1) return fallback;
  const v = Number(process.argv[i + 1]);
  return Number.isFinite(v) ? v : fallback;
}

function main(): void {
  const path = process.argv[2];
  if (path === undefined) {
    process.stderr.write("usage: verdict-pr-comment <verdict.json> [--cap N] [--run-cost N]\n");
    process.exit(2);
  }

  const a = JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
  const num = (k: string, d = 0): number => (typeof a[k] === "number" ? (a[k] as number) : d);
  const str = (k: string, d = ""): string => (typeof a[k] === "string" ? (a[k] as string) : d);

  const summary: EvalSummary = {
    baseline: str("baseline", "baseline"),
    candidate: str("candidate", "candidate"),
    outcome: str("outcome", "INCONCLUSIVE") as Outcome,
    effect: num("effect"),
    ciLow: num("ci_low"),
    ciHigh: num("ci_high"),
    margin: num("margin", 0.03),
    n: num("n"),
    nDiscordant: num("n_discordant"),
    mcnemarP: num("mcnemar_p", 1),
    baselineRate: num("baseline_rate"),
    candidateRate: num("candidate_rate"),
    canDemonstrateEquivalence: a["can_demonstrate_equivalence"] === true,
    baselineCostUsd: arg("baseline-cost", 0),
    candidateCostUsd: arg("candidate-cost", 0),
    runCostUsd: arg("run-cost", 0),
    runCapUsd: arg("cap", 0),
    sampled: num("n"),
    corpusTotal: arg("corpus-total", 0),
    aborted: str("abort_reason") || undefined,
  };

  process.stdout.write(renderComment(summary));
}

main();
