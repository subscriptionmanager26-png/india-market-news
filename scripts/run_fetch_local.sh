#!/bin/bash
# Run the full news fetch locally (fast, no GitHub Actions rate limits).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${SUPABASE_URL:?Set SUPABASE_URL in .env or environment}"
: "${SUPABASE_SERVICE_ROLE_KEY:?Set SUPABASE_SERVICE_ROLE_KEY in .env or environment}"

exec "$ROOT/.venv/bin/india-market-news" \
  --ticker-csv data/EQUITY_L.csv \
  --series EQ \
  --workers 8 \
  --batch-size 100 \
  --batch-pause 10 \
  --request-delay 0.25
