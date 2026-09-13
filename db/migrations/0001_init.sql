-- ─────────────────────────────────────────────────────────────────────────────
-- Verdict 0001 — initial schema.
--
-- Implements DESIGN.md §5, EXCEPT the tables and columns that need a fixed
-- embedding dimension `D`:
--
--   * corpus_items.embedding
--   * routes  (whole table)
--   * semantic_cache_entries  (whole table)
--
-- `D` is chosen in P2 with the embedding model and is a migration-breaking
-- constant (DECISIONS.md D-015), so those arrive in 0002 rather than being
-- guessed here. Guessing a dimension would mean an index rebuild and a data
-- migration the first time it is wrong.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS vector;

-- Enumerated types keep invalid states unrepresentable at the storage layer,
-- mirroring the Zod / Pydantic unions at the application boundary.
DO $$ BEGIN
  CREATE TYPE task_type AS ENUM
    ('extraction','classification','sql','summarization','support_reply');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE split_kind AS ENUM ('calibration','dev','test');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE verdict_kind AS ENUM ('win','tie','loss');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE cache_kind AS ENUM ('none','exact','semantic');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- Live traffic
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS traces (
  id                 uuid PRIMARY KEY,             -- uuidv7: time-ordered, index-friendly
  request_id         text        NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  route_key          text,
  policy_version     int,
  model_requested    text        NOT NULL,
  model_served       text        NOT NULL,
  provider           text        NOT NULL,
  streamed           boolean     NOT NULL,
  request_hash       bytea       NOT NULL,
  request_body       jsonb       NOT NULL,
  response_body      jsonb,
  status             smallint    NOT NULL,
  error_kind         text,
  finish_reason      text,
  cache_hit          cache_kind  NOT NULL DEFAULT 'none',
  input_tokens       int         NOT NULL DEFAULT 0,
  output_tokens      int         NOT NULL DEFAULT 0,
  cache_read_tokens  int         NOT NULL DEFAULT 0,
  cache_write_tokens int         NOT NULL DEFAULT 0,
  cost_usd           numeric(14,8) NOT NULL DEFAULT 0,
  latency_ms         int,
  ttft_ms            int
);

-- Dashboard landing query: "last N requests". DESC matches the scan direction,
-- so there is no backward scan and no sort node.
CREATE INDEX IF NOT EXISTS traces_created_at_idx ON traces (created_at DESC);

-- Per-route spend and volume over a time window: leading equality on route_key,
-- then range on time. Feeds the Pareto chart's aggregation.
CREATE INDEX IF NOT EXISTS traces_route_time_idx ON traces (route_key, created_at DESC);

-- "Here is a request id from a log line, show me the trace" -- the most-used
-- debugging path. Deliberately NOT unique: an internal retry reuses the id and
-- losing the second attempt to a constraint violation would be worse.
CREATE INDEX IF NOT EXISTS traces_request_id_idx ON traces (request_id);

-- Corpus harvesting: find duplicate live requests when assembling P2's corpus.
CREATE INDEX IF NOT EXISTS traces_hash_idx ON traces (request_hash);

-- PARTIAL. Errors should be a small fraction of rows, so indexing only them
-- keeps the error explorer fast without paying on every successful insert.
CREATE INDEX IF NOT EXISTS traces_errors_idx ON traces (created_at DESC) WHERE status >= 400;

-- Trace search by payload contents. jsonb_path_ops is smaller and faster than
-- the default GIN opclass for pure containment (@>), the only operator the
-- explorer needs.
CREATE INDEX IF NOT EXISTS traces_body_gin ON traces USING gin (request_body jsonb_path_ops);

-- ─────────────────────────────────────────────────────────────────────────────
-- Frozen benchmark corpus  (embedding column added in 0002)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS corpus_items (
  id           uuid PRIMARY KEY,
  slug         text        NOT NULL,
  task_type    task_type   NOT NULL,
  split        split_kind  NOT NULL,
  verifiable   boolean     NOT NULL,
  messages     jsonb       NOT NULL,
  ground_truth jsonb,
  verifier     text,
  source_trace uuid REFERENCES traces (id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT verifiable_has_truth
    CHECK (verifiable = false OR (ground_truth IS NOT NULL AND verifier IS NOT NULL))
);

-- Stable handle so artifacts and tests reference items without leaking UUIDs into git.
CREATE UNIQUE INDEX IF NOT EXISTS corpus_slug_uniq ON corpus_items (slug);

-- "All test-split extraction items" is the inner loop of every fitting job.
-- Both columns are low-cardinality equality filters, so one composite beats a
-- bitmap AND of two scans.
CREATE INDEX IF NOT EXISTS corpus_split_task_idx ON corpus_items (split, task_type);

-- ─────────────────────────────────────────────────────────────────────────────
-- Replay
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS runs (
  id         uuid PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  git_sha    text   NOT NULL,      -- every artifact traces back to a commit
  seed       bigint NOT NULL,
  config     jsonb  NOT NULL,      -- the fully resolved config, not a file path
  status     text   NOT NULL CHECK (status IN ('running','complete','failed'))
);

CREATE TABLE IF NOT EXISTS generations (
  id            uuid PRIMARY KEY,
  run_id        uuid NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
  item_id       uuid NOT NULL REFERENCES corpus_items (id),
  model         text NOT NULL,
  params_hash   bytea NOT NULL,
  replicate_idx smallint NOT NULL,
  output        jsonb,
  stop_reason   text,
  error_kind    text,
  input_tokens  int NOT NULL DEFAULT 0,
  output_tokens int NOT NULL DEFAULT 0,
  cost_usd      numeric(14,8) NOT NULL DEFAULT 0,
  latency_ms    int,
  verifier_pass boolean,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- CORRECTNESS CONSTRAINT, not a performance index. Makes a crashed-and-resumed
-- replay run provably non-duplicating, which is what makes the cost numbers
-- trustworthy.
CREATE UNIQUE INDEX IF NOT EXISTS generations_dedupe_uniq
  ON generations (run_id, item_id, model, params_hash, replicate_idx);

-- The paired bootstrap needs matched pairs per item; this builds them.
CREATE INDEX IF NOT EXISTS generations_item_model_idx ON generations (item_id, model);

-- Content-addressed cache. Outlives runs so a repeat `make bench` costs $0.
CREATE TABLE IF NOT EXISTS generation_cache (
  cache_key  bytea PRIMARY KEY,   -- sha256(model ‖ params_hash ‖ messages ‖ replicate_idx)
  response   jsonb NOT NULL,
  usage      jsonb NOT NULL,
  model      text  NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Judging and calibration
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS judgments (
  id             uuid PRIMARY KEY,
  run_id         uuid NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
  item_id        uuid NOT NULL REFERENCES corpus_items (id),
  candidate_id   uuid NOT NULL REFERENCES generations (id),
  reference_id   uuid NOT NULL REFERENCES generations (id),
  judge_model    text NOT NULL,
  rubric_version text NOT NULL,
  position       char(2) NOT NULL CHECK (position IN ('ab','ba')),
  replicate_idx  smallint NOT NULL,
  verdict        verdict_kind NOT NULL,
  raw            jsonb NOT NULL,
  cost_usd       numeric(14,8) NOT NULL DEFAULT 0,
  created_at     timestamptz NOT NULL DEFAULT now()
);

-- Double-counted judgments silently NARROW confidence intervals -- the exact
-- failure that makes a result look significant when it is not. Enforced in the
-- schema rather than in application code.
CREATE UNIQUE INDEX IF NOT EXISTS judgments_pair_uniq
  ON judgments (candidate_id, reference_id, judge_model, rubric_version, position, replicate_idx);

-- Per-item aggregation and disagreement mining.
CREATE INDEX IF NOT EXISTS judgments_item_idx ON judgments (item_id, verdict);

CREATE TABLE IF NOT EXISTS human_labels (
  id           uuid PRIMARY KEY,
  item_id      uuid NOT NULL REFERENCES corpus_items (id),
  candidate_id uuid NOT NULL REFERENCES generations (id),
  reference_id uuid NOT NULL REFERENCES generations (id),
  labeler      text NOT NULL,
  label        verdict_kind NOT NULL,
  presented_as char(2) NOT NULL CHECK (presented_as IN ('ab','ba')),
  elapsed_ms   int,
  notes        text,
  labeled_at   timestamptz NOT NULL DEFAULT now()
);

-- One human label per pair per labeler: stops the same pair being labelled
-- twice and inflating agreement.
CREATE UNIQUE INDEX IF NOT EXISTS labels_pair_labeler_uniq
  ON human_labels (candidate_id, labeler);
