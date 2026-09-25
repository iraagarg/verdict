#!/usr/bin/env bash
# Apply db/migrations/*.sql to $DATABASE_URL, in order, stopping on the first error.
#
# This is the ONE migration path. Local docker compose runs it through the
# `migrate` service (D-016); CI runs it against a throwaway Postgres; deploy runs
# it against Neon. Three environments, one script, so a migration that works
# locally cannot fail in production for a reason nobody has seen before.
#
# Migrations must be idempotent — CI applies them twice and fails if the second
# pass errors. That is what makes it safe to run this on every deploy rather
# than tracking which migrations have already been applied.
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"

DIR="${1:-db/migrations}"

if ! command -v psql >/dev/null 2>&1; then
  echo "psql not found. Install the postgresql client." >&2
  exit 1
fi

shopt -s nullglob
files=("$DIR"/*.sql)
if [ ${#files[@]} -eq 0 ]; then
  echo "no .sql files in $DIR" >&2
  exit 1
fi

# Never echo DATABASE_URL: on a managed provider it contains the password.
host=$(printf '%s' "$DATABASE_URL" | sed -E 's#.*@([^/:]+).*#\1#')
echo "applying ${#files[@]} migration(s) to ${host}"

for f in "${files[@]}"; do
  echo "  -> $(basename "$f")"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 --quiet -f "$f"
done

echo "migrations complete"
