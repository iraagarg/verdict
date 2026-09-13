-- ─────────────────────────────────────────────────────────────────────────────
-- 0002 — record whether a trace's token usage was confirmed by the provider.
--
-- Anthropic streams usage incrementally; OpenAI and Groq report it once, in a
-- final chunk. A stream that aborts mid-token therefore leaves us with a token
-- count that is a FLOOR, not a fact.
--
-- Without this column every downstream cost number silently averages confirmed
-- and unconfirmed rows together. With it, P2 onwards can filter to
-- `usage_is_final = true` and say so. DECISIONS.md D-017.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE traces
  ADD COLUMN IF NOT EXISTS usage_is_final boolean NOT NULL DEFAULT false;

-- Cost analysis reads only confirmed rows, and they should be the vast
-- majority, so the useful index is the partial one over the exceptions.
CREATE INDEX IF NOT EXISTS traces_unconfirmed_usage_idx
  ON traces (created_at DESC)
  WHERE usage_is_final = false;
