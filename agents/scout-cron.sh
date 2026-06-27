#!/usr/bin/env bash
# scout-cron.sh — Scout → Research pipeline runner
# Invoked by cron (e.g. 0 */6 * * *) or manually.
# Always runs Agent 2 after Agent 1; Agent 2 no-ops if nothing new is ready.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"

echo "$LOG_PREFIX Starting MarketFlow AI repo scout pipeline"

# Load .env from the project root if present (cron doesn't inherit shell env)
if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$PROJECT_ROOT/.env"
  set +a
  echo "$LOG_PREFIX Loaded .env from $PROJECT_ROOT"
fi

# Validate required env vars
: "${SUPABASE_URL:?SUPABASE_URL must be set (see agents/README.md)}"
: "${SUPABASE_SERVICE_KEY:?SUPABASE_SERVICE_KEY must be set (see agents/README.md)}"

# --- Agent 1: Scout ---
echo "$LOG_PREFIX Running Scout Agent..."
claude -p "$(cat "$SCRIPT_DIR/scout-agent-prompt.md")"
echo "$LOG_PREFIX Scout Agent complete"

# --- Agent 2: Research ---
# Always invoke — the research agent queries for status='good' AND researched_at IS NULL,
# so it naturally no-ops if the scout found nothing new this run.
echo "$LOG_PREFIX Running Research Agent..."
claude -p "$(cat "$SCRIPT_DIR/research-agent-prompt.md")"
echo "$LOG_PREFIX Research Agent complete"

echo "$LOG_PREFIX Pipeline done"
