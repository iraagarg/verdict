/**
 * Artifact loading.
 *
 * The dashboard reads COMMITTED FILES, never a database. That is a deliberate
 * constraint (DECISIONS.md D-052): the whole site builds statically, deploys
 * with no connection string and no secrets, and cannot break during a demo
 * because there is nothing live to break.
 *
 * The cost is that traces are a snapshot rather than a live feed, and every
 * page that shows them says so rather than implying otherwise.
 *
 * Missing artifacts are a NORMAL state, not an error. Most of these are
 * produced by runs that cost real money and have not happened yet, so each
 * loader returns null and each page renders a real empty state explaining what
 * would produce the data.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ARTIFACTS_DIR = join(process.cwd(), "..", "..", "artifacts");

function read<T>(name: string): T | null {
  try {
    return JSON.parse(readFileSync(join(ARTIFACTS_DIR, name), "utf8")) as T;
  } catch {
    // Absent or unreadable. Both mean "this measurement has not been made",
    // which the page renders as an empty state rather than a failure.
    return null;
  }
}

export interface VerdictArtifact {
  created_at: string;
  git_sha: string;
  baseline: string;
  candidate: string;
  outcome: "REGRESSION" | "IMPROVEMENT" | "EQUIVALENT" | "INCONCLUSIVE";
  effect: number;
  ci_low: number;
  ci_high: number;
  n: number;
  margin: number;
  statistically_significant: boolean;
  minimum_detectable_effect: number;
  can_demonstrate_equivalence: boolean;
  mcnemar_p: number;
  mcnemar_method: string;
  n_discordant: number;
  n_concordant: number;
  baseline_rate: number;
  candidate_rate: number;
  notes: string[];
}

export interface SweepPoint {
  threshold: number;
  n: number;
  quality: number;
  mean_cost_nano: number;
  escalation_rate: number;
  cost_ratio: number;
  verdict: string;
  effect: number;
  ci_low: number;
  ci_high: number;
  eligible: boolean;
}

export interface ParetoArtifact {
  created_at: string;
  cheap_model: string;
  strong_model: string;
  margin: number;
  fit_split: string;
  report_split: string;
  sweeps: Array<{ split: string; role: "fit" | "report"; n: number; points: SweepPoint[] }>;
  chosen_threshold: number | null;
  held_out: SweepPoint | null;
  no_eligible_threshold_reason: string | null;
}

export interface CacheThresholdPoint {
  threshold: number;
  hit_rate: number;
  false_hit_rate: number;
  false_hit_ci_high: number;
  acceptable: boolean;
}

export interface CacheCalibration {
  created_at: string;
  embedding_model: string;
  embedding_dimensions: number;
  max_false_hit_rate: number;
  min_hit_rate: number;
  n_duplicates: number;
  n_different: number;
  min_negatives_required: number;
  points: CacheThresholdPoint[];
  chosen_threshold: number | null;
  price_of_usefulness: number[][];
}

export interface Trace {
  id: string;
  request_id: string;
  created_at: string;
  route_key: string | null;
  model_requested: string;
  model_served: string;
  provider: string;
  streamed: boolean;
  status: number;
  error_kind: string | null;
  finish_reason: string | null;
  cache_hit: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  usage_is_final: boolean;
  latency_ms: number | null;
  ttft_ms: number | null;
  prompt_preview: string;
  response_text: string;
}

export interface TracesArtifact {
  exported_at: string;
  note: string;
  total_traces_in_db: number;
  sampled: number;
  traces: Trace[];
}

export interface PilotArtifact {
  pass_rate_by_model: Record<string, number>;
  pass_rate_by_model_and_task: Record<string, number>;
  gradable_piloted: number;
  item_outcomes: Record<string, number>;
  pilot_models: string[];
}

export const loadPareto = (): ParetoArtifact | null => read<ParetoArtifact>("pareto.json");
export const loadCache = (): CacheCalibration | null =>
  read<CacheCalibration>("cache-calibration.json");
export const loadTraces = (): TracesArtifact | null => read<TracesArtifact>("traces-sample.json");
export const loadPilot = (): PilotArtifact | null => read<PilotArtifact>("difficulty-pilot.json");

export function loadVerdicts(): VerdictArtifact[] {
  const names = [
    "verdict-claude-haiku-4-5-vs-claude-sonnet-5.json",
    "verdict-openai_gpt-oss-20b-vs-claude-haiku-4-5.json",
  ];
  return names.map((n) => read<VerdictArtifact>(n)).filter((v): v is VerdictArtifact => v !== null);
}

/** USD formatted to the precision the number actually has. */
export function usd(value: number): string {
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(6)}`;
  return `$${value.toFixed(4)}`;
}

export function pct(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function signedPct(value: number, digits = 2): string {
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`;
}
