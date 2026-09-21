-- ─────────────────────────────────────────────────────────────────────────────
-- 0003 — vector columns and the semantic cache.
--
-- Deferred from 0001 because it needs a fixed embedding dimension, and D-015
-- made that a migration-breaking constant: changing it means rewriting three
-- columns and rebuilding two HNSW indexes. D is 384
-- (BAAI/bge-small-en-v1.5, run locally), chosen in D-044 and amended in D-049.
--
-- The amendment was free because it happened while both vector tables were
-- still empty. That window is now closed.
-- ─────────────────────────────────────────────────────────────────────────────

-- Route clustering and corpus-level similarity work.
ALTER TABLE corpus_items
  ADD COLUMN IF NOT EXISTS embedding vector(384);

-- ─────────────────────────────────────────────────────────────────────────────
-- Fitted routing policy (DESIGN.md §5.1)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS routes (
  id             uuid PRIMARY KEY,
  policy_version int  NOT NULL,
  route_key      text NOT NULL,
  centroid       vector(384) NOT NULL,
  task_type      task_type,
  assigned_model text NOT NULL,
  floor_value    real NOT NULL,
  observed_value real NOT NULL,
  ci_low         real NOT NULL,
  ci_high        real NOT NULL,
  n_items        int  NOT NULL,
  q_value        real,
  fitted_at      timestamptz NOT NULL DEFAULT now(),
  git_sha        text NOT NULL
);

-- A policy version is an immutable set of routes: serving reads one version,
-- fitting writes the next, activation flips a pointer. No partially-applied
-- policy is ever visible.
CREATE UNIQUE INDEX IF NOT EXISTS routes_version_key_uniq
  ON routes (policy_version, route_key);

-- HOT PATH once route inference from prompt embeddings lands (D-039 defers it
-- to this phase's embedding model). Nearest-route lookup runs on every
-- auto-routed request, so this index decides whether the gateway's latency
-- budget holds. HNSW for the same reason as the cache index (D-008).
CREATE INDEX IF NOT EXISTS routes_centroid_hnsw
  ON routes USING hnsw (centroid vector_cosine_ops);

-- ─────────────────────────────────────────────────────────────────────────────
-- Semantic cache
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS semantic_cache_entries (
  id             uuid PRIMARY KEY,
  embedding      vector(384) NOT NULL,
  request_hash   bytea NOT NULL,
  model          text  NOT NULL,
  route_key      text,
  response       jsonb NOT NULL,

  -- Invalidation. A cached answer is only valid for the prompt that produced
  -- it: change the system prompt and every stored answer is potentially wrong,
  -- but nothing about the stored row would reveal that. The version is part of
  -- the lookup, so a bump makes old entries unreachable rather than stale.
  prompt_version text NOT NULL DEFAULT 'v1',

  -- Per-key TTL. Stored as an absolute instant rather than a duration so that
  -- expiry does not depend on when the sweeper happens to run.
  expires_at     timestamptz NOT NULL,

  input_tokens   int NOT NULL DEFAULT 0,
  output_tokens  int NOT NULL DEFAULT 0,
  cost_usd       numeric(14,8) NOT NULL DEFAULT 0,
  hits           int NOT NULL DEFAULT 0,
  created_at     timestamptz NOT NULL DEFAULT now(),
  last_hit_at    timestamptz
);

-- HOT PATH. Every semantic-cache probe runs this, so its latency is added to
-- every request that misses the exact cache. HNSW over IVFFlat because IVFFlat
-- needs a populated, representative table at build time and degrades as the
-- distribution shifts — which is exactly what a filling cache does (D-008).
CREATE INDEX IF NOT EXISTS semcache_embedding_hnsw
  ON semantic_cache_entries USING hnsw (embedding vector_cosine_ops);

-- A candidate is only usable if it shares the model AND the prompt version.
-- Filtering those in SQL rather than after the ANN search stops the vector
-- index from returning neighbours that were never eligible.
CREATE INDEX IF NOT EXISTS semcache_scope_idx
  ON semantic_cache_entries (model, prompt_version);

-- TTL sweep. Partial, because only unexpired rows are ever swept and the
-- expired ones are deleted.
CREATE INDEX IF NOT EXISTS semcache_expiry_idx
  ON semantic_cache_entries (expires_at);

-- Exact-duplicate short circuit: if the identical request is already cached
-- under the same model and prompt version, there is nothing to search for.
CREATE UNIQUE INDEX IF NOT EXISTS semcache_exact_uniq
  ON semantic_cache_entries (request_hash, model, prompt_version);
