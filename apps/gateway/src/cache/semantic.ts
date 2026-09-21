/**
 * Near-duplicate lookup over pgvector.
 *
 * The similarity threshold is NOT a constant in this file. It is fitted offline
 * against labelled pairs and arrives with a measured false-hit rate attached
 * (DECISIONS.md D-046); hard-coding a plausible-looking 0.95 here would be
 * exactly the unexamined guess this phase exists to replace.
 *
 * Filtering by model and prompt version happens in SQL rather than after the
 * ANN search. Post-filtering would let the index spend its candidate budget on
 * neighbours that were never eligible, so a genuine duplicate could be pushed
 * out of the result set by rows that get discarded a moment later.
 */
import type { Pool } from "pg";

export interface SemanticCacheConfig {
  /** Fitted offline. A hit requires cosine similarity at or above this. */
  threshold: number;
  /** Recorded so a served hit can be traced back to the calibration that allowed it. */
  calibrationSha: string;
  /** Measured false-hit rate upper bound at this threshold. Reported, never assumed zero. */
  falseHitRateUpperBound: number;
}

export interface SemanticHit {
  id: string;
  response: unknown;
  similarity: number;
  createdAt: Date;
}

export class SemanticCache {
  constructor(
    private readonly pool: Pool,
    private readonly config: SemanticCacheConfig,
  ) {}

  /**
   * Nearest unexpired entry for this model and prompt version, if it clears the
   * threshold. Expiry is filtered in SQL so a lapsed entry can never be served
   * even if the sweeper has not run.
   */
  async lookup(
    embedding: number[],
    model: string,
    promptVersion: string,
  ): Promise<SemanticHit | null> {
    const vector = `[${embedding.join(",")}]`;
    const { rows } = await this.pool.query<{
      id: string;
      response: unknown;
      similarity: string;
      created_at: Date;
    }>(
      `SELECT id, response, 1 - (embedding <=> $1::vector) AS similarity, created_at
         FROM semantic_cache_entries
        WHERE model = $2
          AND prompt_version = $3
          AND expires_at > now()
        ORDER BY embedding <=> $1::vector
        LIMIT 1`,
      [vector, model, promptVersion],
    );

    const row = rows[0];
    if (!row) return null;

    const similarity = Number(row.similarity);
    if (!Number.isFinite(similarity) || similarity < this.config.threshold) return null;

    return { id: row.id, response: row.response, similarity, createdAt: row.created_at };
  }

  /** Record a hit. Best-effort: a failed counter update must never fail a request. */
  async recordHit(id: string): Promise<void> {
    try {
      await this.pool.query(
        `UPDATE semantic_cache_entries SET hits = hits + 1, last_hit_at = now() WHERE id = $1`,
        [id],
      );
    } catch {
      // Analytics, not correctness. DESIGN.md failure mode #7.
    }
  }

  async store(entry: {
    id: string;
    embedding: number[];
    requestHash: Buffer;
    model: string;
    promptVersion: string;
    routeKey: string | null;
    response: unknown;
    expiresAt: Date;
    inputTokens: number;
    outputTokens: number;
    costUsd: number;
  }): Promise<void> {
    await this.pool.query(
      `INSERT INTO semantic_cache_entries
         (id, embedding, request_hash, model, prompt_version, route_key, response,
          expires_at, input_tokens, output_tokens, cost_usd)
       VALUES ($1, $2::vector, $3, $4, $5, $6, $7, $8, $9, $10, $11)
       ON CONFLICT (request_hash, model, prompt_version) DO UPDATE
         SET response = EXCLUDED.response, expires_at = EXCLUDED.expires_at`,
      [
        entry.id,
        `[${entry.embedding.join(",")}]`,
        entry.requestHash,
        entry.model,
        entry.promptVersion,
        entry.routeKey,
        JSON.stringify(entry.response),
        entry.expiresAt,
        entry.inputTokens,
        entry.outputTokens,
        entry.costUsd,
      ],
    );
  }

  /** Delete expired rows. Returns how many went. */
  async sweep(): Promise<number> {
    const { rowCount } = await this.pool.query(
      `DELETE FROM semantic_cache_entries WHERE expires_at <= now()`,
    );
    return rowCount ?? 0;
  }

  /**
   * Make every entry for a prompt version unreachable.
   *
   * Used when a prompt changes and the old answers must not be served. Deleting
   * rather than marking, because a row that exists is a row that can be served
   * by a future bug.
   */
  async invalidatePromptVersion(promptVersion: string): Promise<number> {
    const { rowCount } = await this.pool.query(
      `DELETE FROM semantic_cache_entries WHERE prompt_version = $1`,
      [promptVersion],
    );
    return rowCount ?? 0;
  }
}
