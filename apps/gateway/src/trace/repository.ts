/**
 * Trace rows and their Postgres sink.
 *
 * One multi-row INSERT per batch rather than one statement per trace: the
 * queue exists to keep writes off the request path, and a per-row round trip
 * would put the cost back in a different place.
 */
import type { Pool } from "pg";
import type { FinishReason } from "@verdict/shared";
import type { ProviderName } from "../providers/types.js";

export type CacheKind = "none" | "exact" | "semantic";

export interface TraceRow {
  id: string;
  requestId: string;
  createdAt: Date;
  routeKey: string | null;
  policyVersion: number | null;
  modelRequested: string;
  modelServed: string;
  provider: ProviderName;
  streamed: boolean;
  requestHash: Buffer;
  requestBody: unknown;
  responseBody: unknown;
  status: number;
  errorKind: string | null;
  finishReason: FinishReason | null;
  cacheHit: CacheKind;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  costUsd: number;
  /**
   * False when the provider never confirmed final usage (an aborted stream, a
   * provider that reports only at the end and never got there). Such a row's
   * cost is a floor, not a fact, and P2 onwards must exclude it rather than
   * average it in. DECISIONS.md D-017.
   */
  usageIsFinal: boolean;
  latencyMs: number | null;
  ttftMs: number | null;
}

const COLUMNS = [
  "id",
  "request_id",
  "created_at",
  "route_key",
  "policy_version",
  "model_requested",
  "model_served",
  "provider",
  "streamed",
  "request_hash",
  "request_body",
  "response_body",
  "status",
  "error_kind",
  "finish_reason",
  "cache_hit",
  "input_tokens",
  "output_tokens",
  "cache_read_tokens",
  "cache_write_tokens",
  "cost_usd",
  "usage_is_final",
  "latency_ms",
  "ttft_ms",
] as const;

function values(row: TraceRow): unknown[] {
  return [
    row.id,
    row.requestId,
    row.createdAt,
    row.routeKey,
    row.policyVersion,
    row.modelRequested,
    row.modelServed,
    row.provider,
    row.streamed,
    row.requestHash,
    JSON.stringify(row.requestBody),
    row.responseBody === null ? null : JSON.stringify(row.responseBody),
    row.status,
    row.errorKind,
    row.finishReason,
    row.cacheHit,
    row.inputTokens,
    row.outputTokens,
    row.cacheReadTokens,
    row.cacheWriteTokens,
    row.costUsd,
    row.usageIsFinal,
    row.latencyMs,
    row.ttftMs,
  ];
}

/** Build the sink the TraceQueue writes through. */
export function createTraceSink(pool: Pool): (rows: TraceRow[]) => Promise<void> {
  return async (rows) => {
    if (rows.length === 0) return;

    const params: unknown[] = [];
    const tuples = rows.map((row) => {
      const vals = values(row);
      const placeholders = vals.map((_, i) => `$${params.length + i + 1}`);
      params.push(...vals);
      return `(${placeholders.join(",")})`;
    });

    await pool.query(
      `INSERT INTO traces (${COLUMNS.join(",")}) VALUES ${tuples.join(",")}`,
      params,
    );
  };
}
