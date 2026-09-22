#!/usr/bin/env bash
# Export a sampled slice of traces to a committed artifact.
#
# The dashboard reads committed files rather than querying Postgres, so it
# builds statically and cannot break during a demo (DECISIONS.md D-052). This
# is a SNAPSHOT and the dashboard labels it as one.
#
# Response bodies are truncated to 1200 characters: enough to show the trace
# explorer working on real traffic, without publishing every answer the gateway
# ever produced.
#
# Sampling is STRATIFIED by model. Taking the most recent N gave 200 rows all
# from one model, because the last thing to run was paraphrase generation on a
# single cheap rung — a sample that is real and completely unrepresentative.
set -euo pipefail

PER_MODEL="${1:-60}"
OUT="${2:-artifacts/traces-sample.json}"

docker compose exec -T postgres psql -U verdict -d verdict -tAc "
  SELECT json_build_object(
    'artifact_version', 1,
    'exported_at', to_char(now() at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),
    'note', 'A SNAPSHOT of gateway traffic, not a live feed. Sampled evenly across models, not most-recent-first.',
    'total_traces_in_db', (SELECT count(*) FROM traces),
    'sampled', count(*),
    'traces', coalesce(json_agg(t ORDER BY t.created_at DESC), '[]'::json)
  )
  FROM (
    SELECT
      id, request_id,
      to_char(created_at at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS created_at,
      route_key, model_requested, model_served, provider, streamed, status,
      error_kind, finish_reason, cache_hit,
      input_tokens, output_tokens, cost_usd::float8 AS cost_usd,
      usage_is_final, latency_ms, ttft_ms,
      left(coalesce(request_body->'messages'->-1->>'content', ''), 400) AS prompt_preview,
      left(coalesce(response_body->>'text',
                    response_body->'choices'->0->'message'->>'content', ''), 1200) AS response_text
    FROM (
      SELECT *, row_number() OVER (PARTITION BY model_served ORDER BY created_at DESC) AS rn
      FROM traces
    ) ranked
    WHERE rn <= ${PER_MODEL}
  ) t;
" | python3 -m json.tool > "${OUT}"

echo "wrote ${OUT}"
python3 - "${OUT}" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"  {d['sampled']} of {d['total_traces_in_db']} traces")
models = {}
for t in d["traces"]:
    models[t["model_served"]] = models.get(t["model_served"], 0) + 1
print("  by model:", dict(sorted(models.items())))
print("  with response text:", sum(1 for t in d["traces"] if t["response_text"]))
PY
